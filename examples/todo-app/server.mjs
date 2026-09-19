import { createServer } from "node:http";

const port = Number(process.env.PORT || 8080);
let todos = [];
let nextId = 1;

const esc = (s) =>
  String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

function page(filter) {
  const counts = {
    all: todos.length,
    open: todos.filter((t) => !t.done).length,
    done: todos.filter((t) => t.done).length,
  };
  const visible = todos.filter((t) => (filter === "open" ? !t.done : filter === "done" ? t.done : true));
  const link = (name, key) =>
    `<a href="/?filter=${key}" ${filter === key ? 'class="active"' : ""}>${name} (${counts[key]})</a>`;
  const items = visible
    .map(
      (t) => `<li id="todo-${t.id}">
      <span class="text">${esc(t.text)}</span>
      <form method="post" action="/toggle"><input type="hidden" name="id" value="${t.id}">
        <button type="submit">${t.done ? "Mark open" : "Mark done"}</button>
      </form>
      <form method="post" action="/remove"><input type="hidden" name="id" value="${t.id}">
        <button type="submit">Remove</button></form>
    </li>`,
    )
    .join("\n");
  return `<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Todos</title>
<style>body{font-family:sans-serif;max-width:560px;margin:40px auto;padding:0 16px}
li{display:flex;gap:8px;align-items:center;margin:6px 0}.text{flex:1}
.done .text{text-decoration:line-through;color:#888}a{margin-right:12px}
.active{font-weight:700;text-decoration:none;color:inherit}</style></head>
<body>
<h1>Todos</h1>
<form method="post" action="/add">
  <input name="text" placeholder="What needs doing?" aria-label="New todo">
  <button type="submit">Add</button>
</form>
<p>${link("All", "all")} ${link("Open", "open")} ${link("Done", "done")}</p>
<ul>${items || '<li id="empty">Nothing here yet</li>'}</ul>
${counts.done ? `<form method="post" action="/clear-completed"><button type="submit">Clear completed (${counts.done})</button></form>` : ""}
</body></html>`;
}

const server = createServer((req, res) => {
  const url = new URL(req.url, `http://${req.headers.host || "localhost"}`);
  if (url.pathname === "/healthz") {
    res.writeHead(200, { "content-type": "text/plain" });
    return res.end("ok");
  }
  if (req.method === "POST") {
    let body = "";
    req.on("data", (chunk) => (body += chunk));
    req.on("end", () => {
      const form = new URLSearchParams(body);
      if (url.pathname === "/add") {
        const text = (form.get("text") || "").trim();
        if (text) todos.push({ id: nextId++, text, done: false });
      } else if (url.pathname === "/toggle") {
        const todo = todos.find((t) => t.id === Number(form.get("id")));
        if (todo) todo.done = !todo.done;
      } else if (url.pathname === "/remove") {
        todos = todos.filter((t) => t.id !== Number(form.get("id")));
      } else if (url.pathname === "/clear-completed") {
        todos = todos.filter((t) => !t.done);
      }
      res.writeHead(303, { location: "/" });
      res.end();
    });
    return;
  }
  res.writeHead(200, { "content-type": "text/html; charset=utf-8" });
  res.end(page(url.searchParams.get("filter") || "all"));
});

server.listen(port, "0.0.0.0", () => {
  console.log(`todo app listening on http://localhost:${port}`);
});
