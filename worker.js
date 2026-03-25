/**
 * FormFill MCP — Cloudflare Worker
 *
 * Thin proxy that receives MCP requests from remote AI agents and forwards
 * them to the Python FormFill MCP server running on your origin.
 *
 * -------------------------------------------------------------------------
 * HOW TO DEPLOY
 * -------------------------------------------------------------------------
 * 1. Install Wrangler:
 *      npm install -g wrangler
 *
 * 2. Authenticate:
 *      wrangler login
 *
 * 3. Set the origin URL as a secret (replace with your actual server URL):
 *      wrangler secret put FORMFILL_ORIGIN_URL
 *      > https://your-server.example.com
 *
 * 4. Deploy:
 *      wrangler deploy
 *
 * 5. Point your Smithery / Claude Desktop config at the Worker URL:
 *      https://<your-worker>.workers.dev/mcp
 * -------------------------------------------------------------------------
 *
 * wrangler.toml (create alongside this file):
 * ---
 * name = "formfill-mcp"
 * main = "worker.js"
 * compatibility_date = "2024-01-01"
 * ---
 */

export default {
  async fetch(request, env) {
    const originUrl = env.FORMFILL_ORIGIN_URL;

    if (!originUrl) {
      return new Response(
        JSON.stringify({ error: "FORMFILL_ORIGIN_URL secret is not configured." }),
        { status: 500, headers: { "Content-Type": "application/json" } }
      );
    }

    // Build the forwarded URL — preserve path and query string
    const url = new URL(request.url);
    const targetUrl = new URL(url.pathname + url.search, originUrl);

    // Forward the request, preserving method, headers, and body
    const forwardedRequest = new Request(targetUrl.toString(), {
      method: request.method,
      headers: request.headers,
      body: request.method !== "GET" && request.method !== "HEAD"
        ? request.body
        : undefined,
    });

    try {
      const response = await fetch(forwardedRequest);
      return response;
    } catch (err) {
      return new Response(
        JSON.stringify({ error: `Failed to reach FormFill origin: ${err.message}` }),
        { status: 502, headers: { "Content-Type": "application/json" } }
      );
    }
  },
};
