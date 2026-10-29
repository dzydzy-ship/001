# -*- coding: utf-8 -*-
"""测试替身：在没有第三方依赖的机器上，也能把 Agent 的完整流程跑一遍。

为什么需要它：
    本机（以及若干评审机）没有 pyyaml / pydantic / sqlalchemy / httpx，
    而 Agent 的编排逻辑恰恰是最需要验证的部分——它决定了演示时点下去会不会崩。
    这里用极小的替身模块顶掉这些库，让 app.services.agent 能在纯标准库下被 import。

注意：**这些替身只用于测试**，不进入运行时；真实环境仍然用真库。
"""
import sys
import types


# ---------------------------------------------------------------- yaml
def _strip_comment(line: str) -> str:
    out = []
    in_quote = None
    for ch in line:
        if ch in "\"'":
            in_quote = None if in_quote == ch else (ch if not in_quote else in_quote)
        if ch == "#" and not in_quote:
            break
        out.append(ch)
    return "".join(out).rstrip()


def _parse_scalar(raw: str):
    text = raw.strip()
    if text == "" or text == "~":
        return None
    if (text.startswith('"') and text.endswith('"')) or (text.startswith("'") and text.endswith("'")):
        return text[1:-1]
    low = text.lower()
    if low in ("true", "yes"):
        return True
    if low in ("false", "no"):
        return False
    try:
        return int(text)
    except ValueError:
        pass
    try:
        return float(text)
    except ValueError:
        pass
    return text


def _mini_yaml_load(stream) -> dict:
    """只支持本项目配置用到的子集：嵌套字典、列表、折叠块 >-。"""
    text = stream.read() if hasattr(stream, "read") else stream
    lines = [ln.rstrip("\n").rstrip("\r") for ln in text.splitlines()]
    root: dict = {}
    stack = [(-1, root)]
    i = 0
    while i < len(lines):
        raw = lines[i]
        i += 1
        if not _strip_comment(raw).strip():
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        body = _strip_comment(raw).strip()

        while stack and indent <= stack[-1][0]:
            stack.pop()
        parent = stack[-1][1]

        if body.startswith("- "):
            if isinstance(parent, list):
                parent.append(_parse_scalar(body[2:]))
            continue

        if ":" not in body:
            continue

        key, _, rest = body.partition(":")
        key = _parse_scalar(key)
        rest = rest.strip()

        if rest in (">-", ">", "|", "|-"):
            chunk = []
            while i < len(lines):
                nxt = lines[i]
                if not _strip_comment(nxt).strip():
                    i += 1
                    continue
                nxt_indent = len(nxt) - len(nxt.lstrip(" "))
                if nxt_indent <= indent:
                    break
                chunk.append(nxt.strip())
                i += 1
            parent[key] = (" " if rest in (">", ">-") else "\n").join(chunk)
            continue

        if rest == "":
            # 看下一行的第一个有效行决定是 dict 还是 list
            j = i
            child_is_list = False
            while j < len(lines):
                probe = lines[j]
                if not _strip_comment(probe).strip():
                    j += 1
                    continue
                probe_indent = len(probe) - len(probe.lstrip(" "))
                if probe_indent <= indent:
                    break
                child_is_list = _strip_comment(probe).strip().startswith("- ")
                break
            container = [] if child_is_list else {}
            parent[key] = container
            stack.append((indent, container))
            continue

        parent[key] = _parse_scalar(rest)
    return root


def install_yaml() -> None:
    mod = types.ModuleType("yaml")
    mod.safe_load = _mini_yaml_load
    mod.load = _mini_yaml_load
    sys.modules["yaml"] = mod


# ---------------------------------------------------------------- pydantic
def install_pydantic() -> None:
    mod = types.ModuleType("pydantic")

    class BaseModel:
        def __init__(self, **kwargs):
            annotations = {}
            for klass in reversed(type(self).__mro__):
                annotations.update(getattr(klass, "__annotations__", {}))
            for name in annotations:
                default = getattr(type(self), name, None)
                setattr(self, name, default)
            for key, value in kwargs.items():
                setattr(self, key, value)

        @classmethod
        def model_validate(cls, data):
            return cls(**(data or {}))

        def model_dump(self):
            annotations = {}
            for klass in reversed(type(self).__mro__):
                annotations.update(getattr(klass, "__annotations__", {}))
            return {name: getattr(self, name, None) for name in annotations}

        @classmethod
        def model_json_schema(cls):
            return {"type": "object", "properties": {}}

    def Field(default=None, default_factory=None, **kwargs):
        if default is not None:
            return default
        return default_factory() if default_factory else None

    mod.BaseModel = BaseModel
    mod.Field = Field
    sys.modules["pydantic"] = mod


# ---------------------------------------------------------------- httpx
def install_httpx() -> None:
    mod = types.ModuleType("httpx")

    class Client:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def post(self, *args, **kwargs):  # pragma: no cover - 测试里不会真发请求
            raise RuntimeError("测试环境不发起真实网络请求")

    mod.Client = Client
    sys.modules["httpx"] = mod


# ---------------------------------------------------------------- sqlalchemy
class FakeColumn:
    def __init__(self, *args, **kwargs):
        self.args = args
        self.kwargs = kwargs
        self.name = None
        self.default = kwargs.get("default")

    def __set_name__(self, owner, name):
        self.name = name

    def __get__(self, instance, owner=None):
        if instance is None:
            return self
        return instance.__dict__.get(self.name, self.default() if callable(self.default) else None)

    def __set__(self, instance, value):
        instance.__dict__[self.name] = value

    def is_(self, other):
        return ("is", self.name, other)

    def __eq__(self, other):
        return ("eq", self.name, other)

    def __hash__(self):
        return id(self)


def install_sqlalchemy() -> None:
    sa = types.ModuleType("sqlalchemy")
    orm = types.ModuleType("sqlalchemy.orm")

    class Session:  # 类型标注用的占位
        pass

    def declarative_base():
        class Base:
            metadata = types.SimpleNamespace(create_all=lambda **kw: None)

            def __init__(self, **kwargs):
                for key, value in kwargs.items():
                    setattr(self, key, value)

        return Base

    def sessionmaker(**kwargs):
        return lambda **inner: None

    def create_engine(*args, **kwargs):
        return types.SimpleNamespace()

    for name in ("Boolean", "Column", "DateTime", "Float", "Integer", "JSON", "String", "Text"):
        setattr(sa, name, FakeColumn)
    sa.create_engine = create_engine
    orm.Session = Session
    orm.declarative_base = declarative_base
    orm.sessionmaker = sessionmaker
    sa.orm = orm
    sys.modules["sqlalchemy"] = sa
    sys.modules["sqlalchemy.orm"] = orm


class FakeQuery:
    def __init__(self, rows):
        self.rows = list(rows)
        self.conditions = []

    def filter(self, *conditions):
        self.conditions.extend([c for c in conditions if c is not None])
        return self

    def order_by(self, *args):
        return self

    def limit(self, *args):
        return self

    def _match(self, row) -> bool:
        for cond in self.conditions:
            kind, name, value = cond
            if kind == "eq" and getattr(row, name, None) != value:
                return False
            if kind == "is" and value is True and getattr(row, name, None) not in (True, 1):
                return False
        return True

    def first(self):
        for row in self.rows:
            if self._match(row):
                return row
        return None

    def all(self):
        return [row for row in self.rows if self._match(row)]

    def count(self):
        return len(self.all())


class FakeDB:
    """最小的"假数据库"：够 agent.handle 用即可。"""

    def __init__(self):
        self.tables = {}
        self._auto = 0

    def query(self, model):
        return FakeQuery(self.tables.get(model.__name__, []))

    def add(self, row):
        self.tables.setdefault(type(row).__name__, []).append(row)

    def commit(self):
        pass

    def refresh(self, row):
        if getattr(row, "id", None) is None:
            self._auto += 1
            row.id = self._auto

    def get(self, model, key):
        for row in self.tables.get(model.__name__, []):
            if getattr(row, "id", None) == key:
                return row
        return None


def install_fastapi() -> None:
    """让路由模块能在无依赖环境里被 import（只测纯函数，不发请求）。"""
    import sys
    import types

    if "fastapi" in sys.modules and getattr(sys.modules["fastapi"], "__stub__", False):
        return

    module = types.ModuleType("fastapi")

    class HTTPException(Exception):
        def __init__(self, status_code: int = 400, detail: str = ""):
            super().__init__(detail)
            self.status_code = status_code
            self.detail = detail

    class APIRouter:
        def __init__(self, *args, **kwargs):
            self.routes = []

        def _register(self, method, path):
            def decorator(func):
                self.routes.append((method, path, func))
                return func

            return decorator

        def get(self, path, **kwargs):
            return self._register("GET", path)

        def post(self, path, **kwargs):
            return self._register("POST", path)

        def put(self, path, **kwargs):
            return self._register("PUT", path)

        def delete(self, path, **kwargs):
            return self._register("DELETE", path)

    def Depends(dependency=None, **kwargs):  # noqa: N802 - 模拟 fastapi 接口
        return dependency

    module.APIRouter = APIRouter
    module.HTTPException = HTTPException
    module.Depends = Depends
    module.__stub__ = True
    sys.modules["fastapi"] = module


def install_all() -> None:
    install_yaml()
    install_pydantic()
    install_httpx()
    install_sqlalchemy()
    install_fastapi()
