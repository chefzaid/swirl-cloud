// Cloudflare Worker deployed by scripts/configure-outage-watch.py (ADR 0030).
// Checks the public endpoints every five minutes from outside the cluster and
// emails changes through the platform mail relay, even while the cluster is down.
import { connect } from 'cloudflare:sockets';
import { DurableObject } from 'cloudflare:workers';

const FAILED_CHECKS_BEFORE_DOWN = 2;
const PROBE_TIMEOUT_MS = 15000;
const DRILL_ENDPOINT = 'drill: simulated endpoint';

export default {
  async scheduled(controller, env, ctx) {
    const watch = env.WATCH.get(env.WATCH.idFromName('watch'));
    ctx.waitUntil(watch.check(controller.scheduledTime));
  },
};

// One instance keeps every endpoint's state between checks.
export class Watch extends DurableObject {
  async check(now) {
    const endpoints = JSON.parse(this.env.ENDPOINTS);
    const results = await Promise.all(endpoints.map(probe));
    const drillUntil = Number(this.env.DRILL_UNTIL || 0);
    if (drillUntil) {
      const ok = now >= drillUntil;
      results.push({ url: DRILL_ENDPOINT, ok, detail: ok ? 'drill over' : `simulated failure until ${clock(drillUntil)}` });
    }
    const previous = (await this.ctx.storage.get('endpoints')) || {};
    const next = {};
    const changes = [];
    for (const result of results) {
      const before = previous[result.url] || { down: false, failures: 0, since: now };
      const failures = result.ok ? 0 : before.failures + 1;
      const down = !result.ok && (before.down || failures >= FAILED_CHECKS_BEFORE_DOWN);
      next[result.url] = { down, failures, since: down === before.down ? before.since : now, detail: result.detail };
      if (down !== before.down) changes.push({ ...result, down, since: before.since });
    }
    if (changes.length) {
      try {
        await sendMail(this.env, changeReport(this.env, changes, next, now));
      } catch (error) {
        // Keep the old state of the changed endpoints so the next check retries.
        for (const change of changes) {
          next[change.url] = { ...(previous[change.url] || { down: false, since: now }), failures: next[change.url].failures };
        }
        console.error(`Outage watch could not send its email: ${error.message}`);
      }
    }
    await this.ctx.storage.put('endpoints', next);
    if (this.env.REPORT_ID && (await this.ctx.storage.get('report')) !== this.env.REPORT_ID) {
      try {
        await sendMail(this.env, statusReport(this.env, next, now));
        await this.ctx.storage.put('report', this.env.REPORT_ID);
      } catch (error) {
        console.error(`Outage watch could not send the status report: ${error.message}`);
      }
    }
    return { checked: results.length, changed: changes.length };
  }
}

async function probe(url) {
  const target = new URL(url);
  // A fresh query string keeps cached pages from hiding an origin failure.
  target.searchParams.set('outage-watch', Date.now().toString(36));
  try {
    const response = await fetch(target, {
      redirect: 'manual',
      signal: AbortSignal.timeout(PROBE_TIMEOUT_MS),
      headers: { 'User-Agent': 'platform-outage-watch' },
    });
    await response.body?.cancel();
    return { url, ok: response.status < 500, detail: `HTTP ${response.status}` };
  } catch (error) {
    const timedOut = error.name === 'TimeoutError' || error.name === 'AbortError';
    return { url, ok: false, detail: timedOut ? `no answer within ${PROBE_TIMEOUT_MS / 1000} s` : 'connection failed' };
  }
}

function clock(time) {
  return `${new Date(time).toISOString().slice(11, 16)} UTC`;
}

function duration(milliseconds) {
  const minutes = Math.max(1, Math.round(milliseconds / 60000));
  return minutes < 120 ? `${minutes} minutes` : `${Math.round(minutes / 60)} hours`;
}

function changeReport(env, changes, state, now) {
  const down = Object.keys(state).filter((url) => state[url].down);
  const total = Object.keys(state).length;
  const lines = changes.map((change) => (change.down
    ? `DOWN  ${change.url}  ${change.detail} (failed the last ${FAILED_CHECKS_BEFORE_DOWN} checks)`
    : `UP    ${change.url}  ${change.detail} (down for ${duration(now - change.since)})`));
  // Mark drill emails so they are not read as an outage.
  const drill = changes.every((change) => change.url === DRILL_ENDPOINT) ? 'Drill: ' : '';
  return {
    subject: drill + (down.length
      ? `[DOWN] ${down.length} of ${total} ${env.SITE} endpoints unreachable`
      : `[UP] every ${env.SITE} endpoint answers again`),
    text: [`Outage watch for ${env.SITE}, checked from Cloudflare at ${clock(now)}.`, '', ...lines, '',
      `Down now: ${down.length ? down.join(', ') : 'none'}.`, footer(total)].join('\n'),
  };
}

function statusReport(env, state, now) {
  const urls = Object.keys(state);
  const answering = urls.filter((url) => !state[url].failures).length;
  // FAIL: the last check failed, but not yet twice in a row.
  const label = (entry) => (entry.down ? 'DOWN' : entry.failures ? 'FAIL' : 'UP  ');
  return {
    subject: `[STATUS] ${answering} of ${urls.length} ${env.SITE} endpoints answer`,
    text: [`Outage watch for ${env.SITE}, checked from Cloudflare at ${clock(now)}.`, '',
      ...urls.map((url) => `${label(state[url])}  ${url}  ${state[url].detail}`), '', footer(urls.length)].join('\n'),
  };
}

function footer(total) {
  return `${total} endpoints are checked every five minutes from outside the cluster; `
    + 'an endpoint is down after two failed checks in a row. See docs/guides/service-levels.md#outage-watch.';
}

// Minimal SMTP client: implicit TLS or STARTTLS, AUTH PLAIN, one message.
async function sendMail(env, { subject, text }) {
  const implicitTls = env.SMTP_TLS === 'ssl';
  const domain = env.MAIL_FROM.split('@')[1];
  let socket = connect({ hostname: env.SMTP_HOST, port: Number(env.SMTP_PORT) },
    { secureTransport: implicitTls ? 'on' : 'starttls' });
  try {
    let session = new SmtpSession(socket);
    await session.expect(220);
    await session.command(`EHLO ${domain}`, 250);
    if (!implicitTls) {
      await session.command('STARTTLS', 220);
      session.release();
      socket = socket.startTls();
      session = new SmtpSession(socket);
      await session.command(`EHLO ${domain}`, 250);
    }
    await session.command(`AUTH PLAIN ${base64(`\u0000${env.SMTP_USERNAME}\u0000${env.SMTP_PASSWORD}`)}`, 235);
    await session.command(`MAIL FROM:<${env.MAIL_FROM}>`, 250);
    for (const recipient of env.MAIL_TO.split(',')) {
      await session.command(`RCPT TO:<${recipient}>`, [250, 251]);
    }
    await session.command('DATA', 354);
    await session.write(`${message(env, domain, subject, text)}\r\n.\r\n`);
    await session.expect(250);
    await session.command('QUIT', 221).catch(() => {});
  } finally {
    await socket.close().catch(() => {});
  }
}

class SmtpSession {
  constructor(socket) {
    this.reader = socket.readable.getReader();
    this.writer = socket.writable.getWriter();
    this.decoder = new TextDecoder();
    this.buffer = '';
  }

  // Returns the code of the next complete reply; errors name only the code.
  async expect(codes) {
    const allowed = [].concat(codes);
    for (;;) {
      const end = this.buffer.indexOf('\r\n');
      if (end >= 0) {
        const line = this.buffer.slice(0, end);
        this.buffer = this.buffer.slice(end + 2);
        if (line[3] === '-') continue;
        const code = Number(line.slice(0, 3));
        if (!allowed.includes(code)) throw new Error(`the relay answered ${code}`);
        return code;
      }
      const { value, done } = await this.reader.read();
      if (done) throw new Error('the relay closed the connection');
      this.buffer += this.decoder.decode(value, { stream: true });
    }
  }

  async write(text) {
    await this.writer.write(new TextEncoder().encode(text));
  }

  async command(line, codes) {
    await this.write(`${line}\r\n`);
    return this.expect(codes);
  }

  release() {
    this.reader.releaseLock();
    this.writer.releaseLock();
  }
}

function message(env, domain, subject, text) {
  const name = env.MAIL_FROM_NAME;
  const from = name ? `${/^[\x20-\x7e]*$/.test(name) ? `"${name}"` : encodeWord(name)} <${env.MAIL_FROM}>` : env.MAIL_FROM;
  return [
    `From: ${from}`,
    `To: ${env.MAIL_TO.split(',').join(', ')}`,
    `Subject: ${encodeWord(subject)}`,
    `Date: ${new Date().toUTCString()}`,
    `Message-ID: <${crypto.randomUUID()}@${domain}>`,
    'MIME-Version: 1.0',
    'Content-Type: text/plain; charset=utf-8',
    'Content-Transfer-Encoding: base64',
    '',
    base64(text).replace(/.{76}/g, '$&\r\n'),
  ].join('\r\n');
}

function encodeWord(value) {
  return /^[\x20-\x7e]*$/.test(value) ? value : `=?UTF-8?B?${base64(value)}?=`;
}

function base64(value) {
  let binary = '';
  for (const byte of new TextEncoder().encode(value)) binary += String.fromCharCode(byte);
  return btoa(binary);
}
