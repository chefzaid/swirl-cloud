// Prometheus metrics for the clamd in the same pod (platform/clamav.yaml):
// whether it answers, its signature version and publication time, its threads,
// queue and memory, and a test scan of the EICAR file once a minute, which
// proves that a stream goes in and a detection comes out. The test scans come
// from 127.0.0.1, which Fluent Bit leaves out of the scan reports.
import http from "node:http";
import net from "node:net";

const CLAMD_HOST = process.env.CLAMD_HOST || "127.0.0.1";
const CLAMD_PORT = Number(process.env.CLAMD_PORT || 3310);
const PORT = Number(process.env.METRICS_PORT || 9464);
const TIMEOUT_MS = Number(process.env.CLAMD_TIMEOUT_MS || 10000);
const CANARY_INTERVAL_MS = Number(process.env.CANARY_INTERVAL_MS || 60000);
// The standard antivirus test file, split so that this source is not itself detected.
const EICAR = Buffer.from("X5O!P%@AP[4\\PZX54(P^)7CC)7}$" + "EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*", "latin1");
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

// One clamd command ("z" form, NUL-terminated); the answer without its NUL.
function command(name, payload) {
  return new Promise((resolve, reject) => {
    const socket = net.connect({ host: CLAMD_HOST, port: CLAMD_PORT });
    const chunks = [];
    const timer = setTimeout(() => socket.destroy(new Error(`${name}: no answer in ${TIMEOUT_MS} ms`)), TIMEOUT_MS);
    socket.on("connect", () => {
      socket.write(`z${name}\0`);
      if (payload) {
        const length = Buffer.alloc(4);
        length.writeUInt32BE(payload.length);
        socket.write(Buffer.concat([length, payload, Buffer.alloc(4)]));
      }
    });
    socket.on("data", (chunk) => chunks.push(chunk));
    socket.on("error", (error) => { clearTimeout(timer); reject(error); });
    socket.on("close", () => {
      clearTimeout(timer);
      resolve(Buffer.concat(chunks).toString("latin1").replace(/\0+$/, "").trim());
    });
  });
}

// "ClamAV 1.5.4/27820/Mon Nov 17 08:21:02 2026": the engine, the daily
// signature version and when it was published (clamd prints UTC here).
export function parseVersion(text) {
  const match = /^ClamAV ([0-9.]+)(?:\/(\d+)\/\w{3} (\w{3}) +(\d{1,2}) (\d{2}):(\d{2}):(\d{2}) (\d{4}))?$/.exec(text);
  if (!match) return null;
  const [, engine, version, month, day, hours, minutes, seconds, year] = match;
  const published = version ? Date.UTC(Number(year), MONTHS.indexOf(month), Number(day),
    Number(hours), Number(minutes), Number(seconds)) / 1000 : null;
  return { engine, version: version ? Number(version) : null, published };
}

const MEGABYTES = { K: 1024, M: 1024 ** 2, G: 1024 ** 3 };

// STATS: "THREADS: live 1  idle 0 max 8 ...", "QUEUE: 0 items",
// "MEMSTATS: ... pools_used 1288.393M pools_total 1288.393M".
export function parseStats(text) {
  const threads = /THREADS: live (\d+)\s+idle (\d+) max (\d+)/.exec(text);
  const queue = /QUEUE: (\d+) items/.exec(text);
  const size = (name) => {
    const match = new RegExp(`${name} ([0-9.]+)([KMG])`).exec(text);
    return match ? Math.round(Number(match[1]) * MEGABYTES[match[2]]) : null;
  };
  return {
    live: threads ? Number(threads[1]) : null,
    idle: threads ? Number(threads[2]) : null,
    max: threads ? Number(threads[3]) : null,
    queue: queue ? Number(queue[1]) : null,
    poolsUsed: size("pools_used"),
    poolsTotal: size("pools_total"),
  };
}

let canary = { detected: null, duration: null, at: 0 };

async function runCanary() {
  const started = process.hrtime.bigint();
  try {
    const answer = await command("INSTREAM", EICAR);
    canary = { detected: /^stream: \S*Eicar\S* FOUND$/i.test(answer) ? 1 : 0, at: Date.now(),
      duration: Number(process.hrtime.bigint() - started) / 1e9 };
    if (!canary.detected) console.error(`Test scan answered "${answer}" instead of the EICAR signature`);
  } catch (error) {
    canary = { detected: 0, duration: null, at: Date.now() };
    console.error(`Test scan failed: ${error.message}`);
  }
}

async function metrics() {
  const lines = [];
  const metric = (name, help, type, samples) => {
    lines.push(`# HELP ${name} ${help}`, `# TYPE ${name} ${type}`);
    for (const [labels, value] of samples) {
      if (value === null || value === undefined || Number.isNaN(value)) continue;
      lines.push(`${name}${labels} ${value}`);
    }
  };
  let version = null;
  let stats = null;
  try {
    version = parseVersion(await command("VERSION"));
    stats = parseStats(await command("STATS"));
  } catch (error) {
    console.error(`clamd did not answer: ${error.message}`);
  }
  // Also while clamd is down, so that a failing test scan never looks current.
  if (Date.now() - canary.at >= CANARY_INTERVAL_MS) await runCanary();
  metric("clamav_up", "Whether clamd answered VERSION and STATS.", "gauge", [["", version && stats ? 1 : 0]]);
  if (version) {
    metric("clamav_build_info", "The clamd engine version.", "gauge", [[`{version="${version.engine}"}`, 1]]);
    metric("clamav_signatures_version", "Version of the loaded daily signature database.", "gauge", [["", version.version]]);
    metric("clamav_signatures_published_timestamp_seconds", "When the loaded daily signatures were published.",
      "gauge", [["", version.published]]);
  }
  if (stats) {
    metric("clamav_threads", "clamd scanning threads by state, and the most it starts.", "gauge",
      [['{state="live"}', stats.live], ['{state="idle"}', stats.idle], ['{state="max"}', stats.max]]);
    metric("clamav_queue_items", "Scans waiting for a free thread.", "gauge", [["", stats.queue]]);
    metric("clamav_memory_pool_used_bytes", "Memory the loaded signatures use.", "gauge", [["", stats.poolsUsed]]);
    metric("clamav_memory_pool_total_bytes", "Memory reserved for the loaded signatures.", "gauge", [["", stats.poolsTotal]]);
  }
  if (canary.at) {
    metric("clamav_canary_detected", "Whether the last test scan of the EICAR file was detected.", "gauge", [["", canary.detected]]);
    metric("clamav_canary_duration_seconds", "How long the last test scan took.", "gauge", [["", canary.duration]]);
    metric("clamav_canary_last_run_timestamp_seconds", "When the last test scan ran.", "gauge", [["", canary.at / 1000]]);
  }
  return lines.join("\n") + "\n";
}

if (process.argv[1] && import.meta.url.endsWith(process.argv[1].split("/").pop())) {
  http.createServer(async (request, response) => {
    if (request.url !== "/metrics") {
      response.writeHead(request.url === "/healthz" ? 200 : 404).end();
      return;
    }
    try {
      const body = await metrics();
      response.writeHead(200, { "Content-Type": "text/plain; version=0.0.4" }).end(body);
    } catch (error) {
      response.writeHead(500).end(String(error));
    }
  }).listen(PORT, () => console.log(`ClamAV metrics on :${PORT}/metrics for clamd at ${CLAMD_HOST}:${CLAMD_PORT}`));
}
