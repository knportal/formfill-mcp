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

const MCP_BROWSER_PAGE = `<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>FormFill MCP Endpoint</title>
  <style>
    body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; max-width: 640px; margin: 80px auto; padding: 0 24px; color: #1a1a1a; }
    h1 { font-size: 1.5rem; margin-bottom: 8px; }
    p { color: #555; line-height: 1.6; }
    code { background: #f4f4f4; border-radius: 4px; padding: 2px 6px; font-family: "SF Mono", monospace; font-size: 0.9em; }
    .box { background: #f9f9f9; border: 1px solid #e0e0e0; border-radius: 8px; padding: 20px 24px; margin: 24px 0; }
    a { color: #0070f3; text-decoration: none; }
    a:hover { text-decoration: underline; }
  </style>
</head>
<body>
  <h1>FormFill MCP Endpoint</h1>
  <p>This URL is the <strong>Model Context Protocol (MCP) endpoint</strong> for FormFill. It is not meant to be opened in a browser — it requires a compatible MCP client.</p>
  <div class="box">
    <strong>Endpoint URL</strong><br>
    <code>https://formfill.plenitudo.ai/mcp</code>
  </div>
  <p>Use this endpoint with:</p>
  <ul>
    <li><strong>Claude Desktop</strong> — add to <code>claude_desktop_config.json</code></li>
    <li><strong>Cursor, Cline, Continue</strong> — MCP server config</li>
    <li>Any agent that supports the Model Context Protocol</li>
  </ul>
  <p>Get started and get an API key at <a href="https://formfill.plenitudo.ai">formfill.plenitudo.ai</a>.</p>
</body>
</html>`;

export default {
  async fetch(request, env) {
    const originUrl = env.FORMFILL_ORIGIN_URL;

    if (!originUrl) {
      return new Response(
        JSON.stringify({ error: "FORMFILL_ORIGIN_URL secret is not configured." }),
        { status: 500, headers: { "Content-Type": "application/json" } }
      );
    }

    // Serve a friendly HTML page when a browser visits /mcp directly
    const url = new URL(request.url);
    const accept = request.headers.get("Accept") || "";
    if (url.pathname === "/mcp" && request.method === "GET" && accept.includes("text/html")) {
      return new Response(MCP_BROWSER_PAGE, {
        status: 200,
        headers: { "Content-Type": "text/html; charset=utf-8" },
      });
    }

    // Build the forwarded URL — preserve path and query string
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
