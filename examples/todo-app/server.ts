const port = Number(process.env.PORT || 8080);

type Todo = { id: number; text: string; done: boolean };
let todos: Todo[] = [];
let nextId = 1;

const esc = (s: string) =>
  s.replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]!));

function counts() {
  return {
    all: todos.length,
    open: todos.filter((t) => !t.done).length,
    done: todos.filter((t) => t.done).length,
  };
}

function page(filter: string): string {
  const c = counts();
  const visible = todos.filter((t) => (filter === "open" ? !t.done : filter === "done" ? t.done : true));
  const link = (name: string, key: string) =>
    `<a href="/?filter=${key}" ${filter === key ? 'class="active"' : ""}>${name} (${c[key as "all" | "open" | "done"]})</a>`;
  const items = visible
    .map(
      (t) => `<li id="todo-${t.id}">
      <span class="text">${esc(t.text)}</span>
      <form method="post" action="/toggle"><input type="hidden" name="id" value="${t.id}">
        <button type="submit" name="action" value="${t.done ? "open" : "done"}">${t.done ? "Mark open" : "Mark done"}</button>
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
<ul>${items || "<li id=\"empty\">Nothing here yet</li>"}</ul>
${
  c.done
    ? `<form method="post" action="/clear-completed"><button type="submit">Clear completed (${c.done})</button></form>`
    : ""
}
</body></html>`;
}

Bun.serve({
  port,
  fetch(req) {
    const url = new URL(req.url);
    if (url.pathname === "/healthz") return new Response("ok");
    if (req.method === "POST") {
      const form = async () => new URLSearchParams(await req.text());
      return form().then((body) => {
        if (url.pathname === "/add") {
          const text = (body.get("text") || "").trim();
          if (text) todos.push({ id: nextId++, text, done: false });
        } else if (url.pathname === "/toggle") {
          const todo = todos.find((t) => t.id === Number(body.get("id")));
          if (todo) todo.done = !todo.done;
        } else if (url.pathname === "/remove") {
          todos = todos.filter((t) => t.id !== Number(body.get("id")));
        } else if (url.pathname === "/clear-completed") {
          todos = todos.filter((t) => !t.done);
        }
        return new Response(null, { status: 303, headers: { location: "/" } });
      });
    }
    const filter = url.searchParams.get("filter") || "all";
    return new Response(page(filter), { headers: { "content-type": "text/html; charset=utf-8" } });
  },
});

console.log(`todo app listening on http://localhost:${port}`);
