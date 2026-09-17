import time

import pyatspi

ROLES = {
    "push button",
    "toggle button",
    "menu",
    "menu item",
    "check box",
    "radio button",
    "page tab",
    "list item",
    "combo box",
    "entry",
    "text",
    "terminal",
    "label",
    "link",
    "icon",
    "table cell",
    "tree item",
    "spin button",
}

INVOKE_ROLES = {
    "push button",
    "menu item",
    "check box",
    "radio button",
    "link",
    "page tab",
    "list item",
    "icon",
    "table cell",
    "tree item",
}

MAX_NODES = 900
MAX_MS = 500


def _extents(node):
    try:
        ext = node.queryComponent().getExtents(pyatspi.DESKTOP_COORDS)
    except Exception:
        return None
    if ext.width < 2 or ext.height < 2:
        return None
    return ext.x, ext.y, ext.width, ext.height


def _focused(node):
    try:
        return node.queryState().contains(pyatspi.STATE_FOCUSED)
    except Exception:
        return False


def _value(node):
    try:
        text_iface = node.queryText()
        count = text_iface.characterCount
        start = max(0, count - 700)
        text = text_iface.getText(start, count)
    except Exception:
        return ""
    text = (text or "").replace("\n", " | ").strip()
    return text[-700:]


def _invokable(node):
    try:
        return node.queryAction().nActions > 0
    except Exception:
        return False


def _children(node):
    kids = []
    try:
        count = node.childCount
    except Exception:
        return kids
    for i in range(count):
        try:
            kids.append(node[i])
        except Exception:
            continue
    return kids


def elements():
    out = []
    started = time.time()
    try:
        desktop = pyatspi.Registry.getDesktop(0)
        apps = _children(desktop)
    except Exception:
        return out

    visited = 0
    stack = list(apps)
    while stack and visited < MAX_NODES and (time.time() - started) * 1000 < MAX_MS:
        node = stack.pop()
        visited += 1
        try:
            node.children
        except Exception:
            pass
        try:
            role = node.getRoleName()
            name = (node.name or "").strip()
        except Exception:
            stack.extend(_children(node))
            continue
        if role in ROLES:
            value = _value(node)
            display = name or value
            if display:
                ext = _extents(node)
                if ext:
                    out.append(
                        {
                            "source": "a11y",
                            "role": role,
                            "text": display[:120],
                            "x": ext[0],
                            "y": ext[1],
                            "w": ext[2],
                            "h": ext[3],
                            "invokable": _invokable(node),
                            "focused": _focused(node),
                            "value": value,
                            "node": node,
                        }
                    )
        stack.extend(_children(node))
    return out


def invoke(node):
    try:
        action = node.queryAction()
        if action.nActions > 0:
            action.doAction(0)
            return True
    except Exception:
        pass
    return False
