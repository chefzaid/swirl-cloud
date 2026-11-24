// A minimal SMTP client for the platform mail relay (ADR 0029), without
// dependencies: implicit TLS ("ssl", port 465) or STARTTLS ("starttls", 587),
// AUTH LOGIN, one message to several recipients. The relay's certificate is
// verified. "none" (no TLS) exists for tests only. Errors name the step and the
// reply code, never the credentials.
import net from "node:net";
import tls from "node:tls";

function reader(socket) {
  let buffer = "";
  const waiting = [];
  const lines = [];
  const deliver = () => {
    while (waiting.length && lines.length) waiting.shift()(lines.shift());
  };
  socket.on("data", (chunk) => {
    buffer += chunk.toString("utf8");
    let index;
    while ((index = buffer.indexOf("\r\n")) >= 0) {
      lines.push(buffer.slice(0, index));
      buffer = buffer.slice(index + 2);
    }
    deliver();
  });
  const next = () => new Promise((resolve) => { waiting.push(resolve); deliver(); });
  // A reply is one or more lines; "250-" continues, "250 " ends it.
  return async () => {
    let line = await next();
    while (/^\d{3}-/.test(line)) line = await next();
    return Number(line.slice(0, 3));
  };
}

function connect(options) {
  return new Promise((resolve, reject) => {
    const socket = options.tls === "ssl"
      ? tls.connect({ host: options.host, port: options.port, servername: options.host }, () => resolve(socket))
      : net.connect({ host: options.host, port: options.port }, () => resolve(socket));
    socket.once("error", reject);
    socket.setTimeout(20000, () => socket.destroy(new Error("timed out")));
  });
}

function encodeHeader(value) {
  return /^[\x20-\x7e]*$/.test(value) ? value : `=?UTF-8?B?${Buffer.from(value, "utf8").toString("base64")}?=`;
}

export async function sendMail(relay, mail) {
  let socket = await connect(relay);
  let reply = reader(socket);
  const write = (line) => socket.write(line + "\r\n");
  const expect = async (step, codes) => {
    const code = await reply();
    if (!codes.includes(code)) throw new Error(`SMTP ${step} refused (${code})`);
  };
  try {
    await expect("greeting", [220]);
    const domain = relay.from.split("@")[1] || "localhost";
    write(`EHLO ${domain}`);
    await expect("EHLO", [250]);
    if (relay.tls === "starttls") {
      write("STARTTLS");
      await expect("STARTTLS", [220]);
      socket = await new Promise((resolve, reject) => {
        const secure = tls.connect({ socket, servername: relay.host }, () => resolve(secure));
        secure.once("error", reject);
      });
      reply = reader(socket);
      write(`EHLO ${domain}`);
      await expect("EHLO", [250]);
    }
    if (relay.username) {
      write("AUTH LOGIN");
      await expect("AUTH", [334]);
      write(Buffer.from(relay.username).toString("base64"));
      await expect("AUTH", [334]);
      write(Buffer.from(relay.password || "").toString("base64"));
      await expect("AUTH", [235]);
    }
    write(`MAIL FROM:<${relay.from}>`);
    await expect("MAIL FROM", [250]);
    for (const address of mail.to) {
      write(`RCPT TO:<${address}>`);
      await expect("RCPT TO", [250, 251]);
    }
    write("DATA");
    await expect("DATA", [354]);
    const from = relay.fromName ? `${encodeHeader(relay.fromName)} <${relay.from}>` : relay.from;
    const headers = [`From: ${from}`, `To: ${relay.from}`, `Subject: ${encodeHeader(mail.subject)}`,
      `Date: ${new Date().toUTCString()}`, "MIME-Version: 1.0", "Content-Type: text/plain; charset=utf-8",
      "Content-Transfer-Encoding: base64"];
    const body = Buffer.from(mail.text, "utf8").toString("base64").replace(/(.{76})/g, "$1\r\n");
    socket.write(headers.join("\r\n") + "\r\n\r\n" + body + "\r\n.\r\n");
    await expect("message", [250]);
    write("QUIT");
  } finally {
    socket.end();
  }
}

// Relay settings from the platform-mail Secret's variables, or null when mail is off.
export function relayFromEnvironment(env = process.env) {
  if (env.PLATFORM_MAIL_ENABLED !== "true" || !env.PLATFORM_MAIL_HOST || !env.PLATFORM_MAIL_FROM) return null;
  return { host: env.PLATFORM_MAIL_HOST, port: Number(env.PLATFORM_MAIL_PORT || (env.PLATFORM_MAIL_TLS === "starttls" ? 587 : 465)),
    tls: env.PLATFORM_MAIL_TLS || "ssl", username: env.PLATFORM_MAIL_USERNAME, password: env.PLATFORM_MAIL_PASSWORD,
    from: env.PLATFORM_MAIL_FROM, fromName: env.PLATFORM_MAIL_FROM_NAME };
}
