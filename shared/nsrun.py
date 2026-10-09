"""
Run an original dashboard (sinter/app.py or mbf/app.py) inside the combined app WITHOUT editing the file.

The file on disk is never changed. Each time a page runs, its source is parsed, a handful of mechanical rewrites are
applied in memory, and the result is executed.  The rewrites are:

  1. st.set_page_config(...)            -> removed (the combined app sets the page once)
  2. the dashboard's own <style> block  -> removed (one shared theme is used instead, see shared/theme.py)
  3. the dashboard's own sidebar / nav  -> removed (the combined app draws one sidebar)
  4. st.session_state                   -> a prefixed view of it, so the sinter and MBF dashboards never share a key
                                           (both use names such as `result`, `df`, `nav`, `runs`)
  5. key="..." on Streamlit widgets      -> the same prefix, so widget keys cannot collide either
  5b. old colour / font strings        -> the shared palette and font (chart series, inline HTML; strings only)
  6. blockers() (MBF only)              -> also reports "the sinter model has not been run" when the sinter row
                                           comes from the sinter model

Nothing else is touched: every page function, table, chart and export is the original code.
"""
import ast
import os

import streamlit as st

from shared.theme import restyle_string

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APPS = {
    "sinter": {"dir": os.path.join(ROOT, "sinter"), "file": "app.py", "prefix": "sinter__", "route": "route_sinter"},
    "mbf": {"dir": os.path.join(ROOT, "mbf"), "file": "app.py", "prefix": "mbf__", "route": "route_mbf"},
}
# Streamlit methods that take a `key=` argument. Only these are rewritten, never sorted(..., key=...) and the like.
WIDGET_METHODS = {
    "button", "download_button", "link_button", "checkbox", "toggle", "radio", "selectbox", "multiselect", "slider",
    "select_slider", "number_input", "text_input", "text_area", "date_input", "time_input", "file_uploader",
    "data_editor", "color_picker", "segmented_control", "pills", "feedback", "camera_input", "audio_input",
    "form", "dataframe", "plotly_chart", "pyplot", "altair_chart", "container", "expander", "popover",
    "form_submit_button", "chat_input", "metric", "table",
}


class NS:
    """A prefixed view of st.session_state.  `nav` is redirected to the combined app's own page selector."""

    def __init__(self, prefix, route_key):
        object.__setattr__(self, "_p", prefix)
        object.__setattr__(self, "_route", route_key)

    # -- key mapping
    def _real(self, name):
        return self._route if name == "nav" else self._p + str(name)

    def __getattr__(self, name):
        if name.startswith("__"):
            raise AttributeError(name)
        try:
            if name == "nav":
                return st.session_state.get(self._route, "Dashboard")
            return st.session_state[self._p + name]
        except KeyError:
            raise AttributeError(name)

    def __setattr__(self, name, value):
        st.session_state[self._real(name)] = value

    def __delattr__(self, name):
        try:
            del st.session_state[self._real(name)]
        except KeyError:
            raise AttributeError(name)

    def __getitem__(self, name):
        if name == "nav":
            return st.session_state.get(self._route, "Dashboard")
        return st.session_state[self._p + str(name)]

    def __setitem__(self, name, value):
        st.session_state[self._real(name)] = value

    def __delitem__(self, name):
        del st.session_state[self._real(name)]

    def __contains__(self, name):
        return name == "nav" or (self._p + str(name)) in st.session_state

    def get(self, name, default=None):
        return self[name] if name in self else default

    def pop(self, name, *default):
        if name == "nav":
            return st.session_state.get(self._route, "Dashboard")
        return st.session_state.pop(self._p + str(name), *default)

    def setdefault(self, name, default=None):
        if name not in self:
            self[name] = default
        return self[name]

    def update(self, other=(), **kw):
        for k, v in dict(other, **kw).items():
            self[k] = v

    def keys(self):
        n = len(self._p)
        return [k[n:] for k in list(st.session_state.keys()) if isinstance(k, str) and k.startswith(self._p)]

    def items(self):
        return [(k, self[k]) for k in self.keys()]

    def values(self):
        return [self[k] for k in self.keys()]

    def __iter__(self):
        return iter(self.keys())

    def __len__(self):
        return len(self.keys())


# --------------------------------------------------------------------------- the rewrite
def _is_st_attr(node, attr):
    return (isinstance(node, ast.Attribute) and node.attr == attr
            and isinstance(node.value, ast.Name) and node.value.id == "st")


def _is_call_to_st(node, attr):
    return isinstance(node, ast.Expr) and isinstance(node.value, ast.Call) and _is_st_attr(node.value.func, attr)


def _is_style_markdown(node):
    if not _is_call_to_st(node, "markdown"):
        return False
    a = node.value.args
    return bool(a) and isinstance(a[0], ast.Constant) and isinstance(a[0].value, str) and "<style>" in a[0].value


def _is_dispatch(node):
    """The apps' final page dispatch: sinter `pages[st.session_state.nav]()`, MBF `try: PAGES.get(...)() finally: ...`."""
    if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call) and isinstance(node.value.func, ast.Subscript):
        return True
    if isinstance(node, ast.Try) and node.body:
        b = node.body[0]
        if isinstance(b, ast.Expr) and isinstance(b.value, ast.Call) and isinstance(b.value.func, ast.Call):
            f = b.value.func.func
            return isinstance(f, ast.Attribute) and f.attr == "get" and isinstance(f.value, ast.Name) and f.value.id == "PAGES"
    return False


class _Rewrite(ast.NodeTransformer):
    def __init__(self, with_blocker_hook):
        self.blocker_hook = with_blocker_hook

    def visit_Attribute(self, node):
        self.generic_visit(node)
        if _is_st_attr(node, "session_state"):
            return ast.copy_location(ast.Name(id="_ss", ctx=ast.Load()), node)
        return node

    def visit_Call(self, node):
        self.generic_visit(node)
        if isinstance(node.func, ast.Attribute) and node.func.attr in WIDGET_METHODS:
            for kw in node.keywords:
                if kw.arg == "key" and not isinstance(kw.value, ast.Lambda):
                    kw.value = ast.copy_location(
                        ast.Call(func=ast.Name(id="_wk", ctx=ast.Load()), args=[kw.value], keywords=[]), kw.value)
        return node

    def visit_Constant(self, node):
        # colours / font names of the original dashboards -> the shared palette (strings only, never logic)
        if isinstance(node.value, str):
            new = restyle_string(node.value)
            if new != node.value:
                return ast.copy_location(ast.Constant(new), node)
        return node

    def visit_Expr(self, node):
        v = node.value
        if isinstance(v, ast.Call) and isinstance(v.func, ast.Name) and v.func.id in ("sidebar_nav", "sidebar_footer"):
            return ast.copy_location(ast.Pass(), node)
        return self.generic_visit(node)

    def visit_FunctionDef(self, node):
        self.generic_visit(node)
        if self.blocker_hook and node.name == "blockers":
            node.body.insert(0, ast.copy_location(ast.Assign(
                targets=[ast.Name(id="_extra", ctx=ast.Store())],
                value=ast.Call(func=ast.Name(id="_extra_blockers", ctx=ast.Load()), args=[], keywords=[])), node))
            for sub in ast.walk(node):
                if isinstance(sub, ast.Return) and sub.value is not None:
                    sub.value = ast.copy_location(ast.BinOp(
                        left=ast.Name(id="_extra", ctx=ast.Load()), op=ast.Add(),
                        right=ast.Call(func=ast.Name(id="list", ctx=ast.Load()), args=[sub.value], keywords=[])), sub.value)
        return node


_CODE_CACHE = {}


def _compile(name, render):
    app = APPS[name]
    path = os.path.join(app["dir"], app["file"])
    stamp = os.path.getmtime(path)
    ck = (name, render)
    hit = _CODE_CACHE.get(ck)
    if hit and hit[0] == stamp:
        return hit[1]
    with open(path, "r", encoding="utf-8") as fh:
        source = fh.read()
    tree = ast.parse(source, filename=path)
    kept = []
    for node in tree.body:                                   # module level: remove what the combined app provides
        if _is_call_to_st(node, "set_page_config") or _is_style_markdown(node):
            continue
        if isinstance(node, ast.With) and node.items and _is_st_attr(node.items[0].context_expr, "sidebar"):
            continue
        if not render and (_is_dispatch(node) or _is_call_to_st(node, "markdown")):
            continue
        kept.append(node)
    tree.body = kept
    tree = _Rewrite(with_blocker_hook=(name == "mbf")).visit(tree)
    ast.fix_missing_locations(tree)
    code = compile(tree, path, "exec")
    _CODE_CACHE[ck] = (stamp, code)
    return code


def load_app(name, render=True, extra_blockers=None):
    """Execute a dashboard in its own namespace and return that namespace.
    render=False only initialises its session state and defines its functions (nothing is drawn)."""
    app = APPS[name]
    prefix = app["prefix"]
    ns = {
        "__name__": f"{name}_dashboard",
        "__file__": os.path.join(app["dir"], app["file"]),
        "_ss": NS(prefix, app["route"]),
        "_wk": lambda k, _p=prefix: _p + str(k),
        "_extra_blockers": extra_blockers or (lambda: []),
    }
    exec(_compile(name, render), ns)
    return ns


def state(name):
    """The prefixed session view of one dashboard (usable after load_app has initialised it)."""
    app = APPS[name]
    return NS(app["prefix"], app["route"])
