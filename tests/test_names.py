"""Static check: every name a module in src/ uses is defined, imported or a builtin. Catches a missing import
(e.g. `tempfile` in the report preview) before a user presses the button that needs it."""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.join(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))), "src"))
import ast
import builtins
import glob
import importlib
import os
import unittest

SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
BUILTINS = set(dir(builtins)) | {"__file__", "__name__", "__doc__", "__spec__"}


def bound_names(node):
    """Names a function / class / comprehension binds somewhere inside itself."""
    out = set()
    for x in ast.walk(node):
        if isinstance(x, ast.Name) and isinstance(x.ctx, (ast.Store, ast.Del)):
            out.add(x.id)
        elif isinstance(x, ast.arg):
            out.add(x.arg)
        elif isinstance(x, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            out.add(x.name)
        elif isinstance(x, (ast.Import, ast.ImportFrom)):
            out |= {(a.asname or a.name).split(".")[0] for a in x.names}
        elif isinstance(x, ast.ExceptHandler) and x.name:
            out.add(x.name)
    return out


def undefined(path, module):
    tree = ast.parse(open(path, encoding="utf-8").read())
    namespace = set(vars(module)) | BUILTINS
    bad = set()
    for top in tree.body:
        local = bound_names(top)
        for x in ast.walk(top):
            if isinstance(x, ast.Name) and isinstance(x.ctx, ast.Load):
                if x.id not in namespace and x.id not in local:
                    bad.add((x.id, x.lineno))
    return sorted(bad)


class Names(unittest.TestCase):
    def test_no_undefined_names_in_src(self):
        files = sorted(glob.glob(os.path.join(SRC, "*.py")) + glob.glob(os.path.join(SRC, "mbui", "*.py")))
        self.assertGreater(len(files), 30)
        problems = {}
        for path in files:
            rel = os.path.relpath(path, SRC)
            name = rel[:-3].replace(os.sep, ".")
            if name.endswith("__init__"):
                continue
            mod = importlib.import_module(name)
            bad = undefined(path, mod)
            if bad:
                problems[rel] = bad
        self.assertEqual(problems, {}, "names used but never defined or imported")


if __name__ == "__main__":
    unittest.main(verbosity=0)
