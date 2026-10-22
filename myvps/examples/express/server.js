const express = require('express');
const app = express();
app.disable('x-powered-by');
app.get('/', (_req, res) => res.type('html').send(`<!doctype html>
<html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>starter-app</title><style>body{font:18px system-ui;max-width:700px;margin:12vh auto;padding:24px;
background:#101827;color:#e8edf5}h1{font-size:48px}code{color:#7ee5c4}a{color:#8ecbff}</style>
<h1>starter-app is running.</h1><p>Your Rocky Linux workspace is ready.</p>
<p>This page is served by Express through Nginx.</p>
<p>SSH in as <code>vps</code> on port <code>2222</code>, then edit
<code>/data/workspace/starter-app/server.js</code> and run <code>myvps restart</code>.</p>
<p>Run <code>myvps help</code> for workspace commands.</p></html>`));
app.get('/health', (_req, res) => res.json({ status: 'ok', runtime: 'node', service: 'starter-app' }));
const server = app.listen(Number(process.env.PORT || 3000), '127.0.0.1', () => {
  console.log(`Express listening on ${server.address().port}`);
});
for (const signal of ['SIGTERM', 'SIGINT']) {
  process.on(signal, () => server.close(() => process.exit(0)));
}
