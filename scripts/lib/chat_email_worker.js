// Cloudflare Email Worker deployed by scripts/configure-odoo-intake.py.
// Relays replies to website chats (chat+<chat>.<audience>.<signature>@domain)
// to Odoo, which checks the signature and the sender before posting them.
export default {
  async email(message, env) {
    const response = await fetch(env.ODOO_INBOUND_URL, {
      method: 'POST',
      headers: {
        Authorization: `Bearer ${env.INBOUND_SECRET}`,
        'Content-Type': 'message/rfc822',
        'X-Envelope-To': message.to,
      },
      body: await new Response(message.raw).arrayBuffer(),
    });
    if (response.status === 403) {
      message.setReject('This conversation only accepts replies from its participants.');
    } else if (!response.ok) {
      // A temporary failure: the sending server retries later.
      throw new Error(`Odoo answered ${response.status}`);
    }
  },
};
