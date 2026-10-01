"""Bounded Python subset for RGB motor programs; no host Python objects exposed.

This interpreter never evals/execs source. Attributes, imports, exceptions, classes,
comprehensions and indirect calls are absent. It is a language boundary, not an OS
sandbox. Only JSON values cross primitive calls; the backend remains private.
"""
import ast
import inspect
import json
import math
import operator
from .contracts import SkillError


class Returned(Exception):
    def __init__(self, value): self.value = value


class BreakLoop(Exception): pass
class ContinueLoop(Exception): pass


class MotorProgram:
    def __init__(self, source, primitives, *, max_operations=10000):
        self.primitives = primitives
        self.remaining = max_operations
        self.depth = 0
        if not isinstance(source, str) or len(source) > 12000:
            raise SkillError('invalid_program', 'Program must be at most 12000 characters')
        try: self.tree = ast.parse(source)
        except SyntaxError as exc:
            raise SkillError('invalid_program', f'Syntax error on line {exc.lineno}') from None
        self.functions = {}
        allowed = (ast.Module, ast.FunctionDef, ast.arguments, ast.arg, ast.Return,
                   ast.Assign, ast.AugAssign, ast.Expr, ast.If, ast.For, ast.While,
                   ast.Break, ast.Continue, ast.Pass, ast.Call, ast.keyword, ast.Name,
                   ast.Load, ast.Store, ast.Constant, ast.List, ast.Tuple, ast.Dict,
                   ast.Subscript, ast.BinOp, ast.UnaryOp, ast.BoolOp, ast.Compare,
                   ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Mod, ast.USub, ast.UAdd,
                   ast.Not, ast.And, ast.Or, ast.Eq, ast.NotEq, ast.Lt, ast.LtE,
                   ast.Gt, ast.GtE, ast.In, ast.NotIn)
        for node in ast.walk(self.tree):
            if not isinstance(node, allowed):
                raise SkillError('invalid_program', f'Unsupported syntax: {type(node).__name__}')
            if isinstance(node, ast.Name) and node.id.startswith('_'):
                raise SkillError('invalid_program', 'Private names are unavailable')
            if isinstance(node, ast.Call) and (not isinstance(node.func, ast.Name) or any(k.arg is None for k in node.keywords)):
                raise SkillError('invalid_program', 'Use direct named calls without keyword expansion')
            if isinstance(node, ast.Assign) and (len(node.targets) != 1 or not isinstance(node.targets[0], ast.Name)):
                raise SkillError('invalid_program', 'Assign to a single local variable')
            if isinstance(node, (ast.For, ast.AugAssign)) and not isinstance(node.target, ast.Name):
                raise SkillError('invalid_program', 'Loop/update target must be a local variable')
            if isinstance(node,(ast.For,ast.While)) and node.orelse:
                raise SkillError('invalid_program','Loop else is unsupported')
            if isinstance(node,ast.Dict) and any(k is None for k in node.keys):
                raise SkillError('invalid_program','Dictionary expansion is unsupported')
            if isinstance(node,ast.FunctionDef) and node not in self.tree.body:
                raise SkillError('invalid_program','Define helpers at top level')
        for node in self.tree.body:
            if not isinstance(node, ast.FunctionDef):
                raise SkillError('invalid_program', 'Top level must contain only function definitions')
            args = node.args
            if (node.decorator_list or args.posonlyargs or args.kwonlyargs or args.vararg or args.kwarg
                    or node.returns or any(a.annotation for a in args.args)):
                raise SkillError('invalid_program', 'Use plain functions with positional parameters and optional defaults')
            if node.name in self.functions or node.name in primitives or node.name in self.builtins or node.name.startswith('_'):
                raise SkillError('invalid_program', 'Duplicate or reserved function name')
            self.functions[node.name] = node
        if 'run' not in self.functions or self.functions['run'].args.args:
            raise SkillError('invalid_program', 'Define run() with no arguments')

    @staticmethod
    def bounded_range(*args):
        result = range(*args)
        if len(result) > 256: raise SkillError('program_limit', 'range has at most 256 entries')
        return list(result)

    builtins = {'abs': abs, 'min': min, 'max': max, 'round': round, 'len': len,
                'int': int, 'float': float, 'range': bounded_range.__func__,
                'sin': math.sin, 'cos': math.cos, 'sqrt': math.sqrt}
    binary = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
              ast.Div: operator.truediv, ast.Mod: operator.mod}
    compare = {ast.Eq: operator.eq, ast.NotEq: operator.ne, ast.Lt: operator.lt,
               ast.LtE: operator.le, ast.Gt: operator.gt, ast.GtE: operator.ge,
               ast.In: lambda a,b: a in b, ast.NotIn: lambda a,b: a not in b}

    def tick(self):
        self.remaining -= 1
        if self.remaining < 0: raise SkillError('program_limit', 'Program computation budget exhausted')

    def value(self, value):
        encoded = json.dumps(value, allow_nan=False)
        if len(encoded) > 16000: raise SkillError('program_limit', 'Value is too large')
        if type(value) is int and value.bit_length() > 64:
            raise SkillError('program_limit', 'Integer exceeds 64 bits')
        return value

    def call(self, name, args, kwargs):
        self.tick()
        if name in self.primitives or name in self.builtins:
            return self.value((self.primitives.get(name) or self.builtins[name])(*args, **kwargs))
        if name not in self.functions: raise SkillError('invalid_program', f'Unknown function: {name}')
        self.depth += 1
        try:
            if self.depth > 16: raise SkillError('program_limit', 'Function nesting exceeds 16')
            node = self.functions[name]
            defaults = [self.expr(n, {}) for n in node.args.defaults]
            parameters = [inspect.Parameter(a.arg, inspect.Parameter.POSITIONAL_OR_KEYWORD,
                default=defaults[i-(len(node.args.args)-len(defaults))] if i >= len(node.args.args)-len(defaults)
                else inspect.Parameter.empty) for i,a in enumerate(node.args.args)]
            bound = inspect.Signature(parameters).bind(*args, **kwargs); bound.apply_defaults()
            try: self.block(node.body, dict(bound.arguments))
            except Returned as result: return self.value(result.value)
            return None
        finally: self.depth -= 1

    def arithmetic(self, op, a, b):
        if type(a) not in (int,float) or type(b) not in (int,float):
            raise SkillError('invalid_program', 'Arithmetic operands must be numbers')
        return self.value(self.binary[type(op)](a,b))

    def expr(self, node, scope):
        self.tick()
        if isinstance(node, ast.Constant):
            if type(node.value) not in (str,int,float,bool,type(None)):
                raise SkillError('invalid_program', 'Use JSON constants')
            return self.value(node.value)
        if isinstance(node, ast.Name): return scope[node.id]
        if isinstance(node, (ast.List,ast.Tuple)): return self.value([self.expr(n,scope) for n in node.elts])
        if isinstance(node, ast.Dict):
            return self.value({self.expr(k,scope):self.expr(v,scope) for k,v in zip(node.keys,node.values)})
        if isinstance(node, ast.Subscript): return self.expr(node.value,scope)[self.expr(node.slice,scope)]
        if isinstance(node, ast.Call):
            return self.call(node.func.id,[self.expr(n,scope) for n in node.args],
                             {k.arg:self.expr(k.value,scope) for k in node.keywords})
        if isinstance(node, ast.BinOp): return self.arithmetic(node.op,self.expr(node.left,scope),self.expr(node.right,scope))
        if isinstance(node, ast.UnaryOp):
            value = self.expr(node.operand,scope)
            if isinstance(node.op,ast.Not): return not value
            return self.arithmetic(ast.Mult(),value,-1 if isinstance(node.op,ast.USub) else 1)
        if isinstance(node, ast.BoolOp):
            for item in node.values:
                value = self.expr(item,scope)
                if isinstance(node.op,ast.And) and not value: return value
                if isinstance(node.op,ast.Or) and value: return value
            return value
        if isinstance(node, ast.Compare):
            previous=self.expr(node.left,scope)
            for op,item in zip(node.ops,node.comparators):
                current=self.expr(item,scope)
                if not self.compare[type(op)](previous,current): return False
                previous=current
            return True
        raise SkillError('invalid_program', 'Unsupported expression')

    def block(self, nodes, scope):
        for node in nodes:
            self.tick()
            if isinstance(node,ast.Return): raise Returned(self.expr(node.value,scope) if node.value else None)
            elif isinstance(node,ast.Assign): scope[node.targets[0].id]=self.expr(node.value,scope)
            elif isinstance(node,ast.AugAssign): scope[node.target.id]=self.arithmetic(node.op,scope[node.target.id],self.expr(node.value,scope))
            elif isinstance(node,ast.Expr): self.expr(node.value,scope)
            elif isinstance(node,ast.If): self.block(node.body if self.expr(node.test,scope) else node.orelse,scope)
            elif isinstance(node,(ast.For,ast.While)):
                iterator=iter(self.expr(node.iter,scope)) if isinstance(node,ast.For) else None
                while True:
                    self.tick()
                    if iterator is not None:
                        try: scope[node.target.id]=next(iterator)
                        except StopIteration: break
                    elif not self.expr(node.test,scope): break
                    try: self.block(node.body,scope)
                    except BreakLoop: break
                    except ContinueLoop: continue
            elif isinstance(node,ast.Break): raise BreakLoop()
            elif isinstance(node,ast.Continue): raise ContinueLoop()
            elif isinstance(node,ast.Pass): pass
            else: raise SkillError('invalid_program', 'Nested definitions are unsupported')

    def run(self):
        try:
            result=self.call('run',[],{})
            if not isinstance(result,dict) or result.get('status') not in {'completed','partial','blocked'} or not isinstance(result.get('reason'),str) or not result['reason']:
                raise SkillError('invalid_program_result', 'Return status completed/partial/blocked and a nonempty reason')
            return result
        except SkillError: raise
        except (KeyError,TypeError,ValueError,IndexError,ZeroDivisionError,OverflowError,BreakLoop,ContinueLoop):
            raise SkillError('program_error', 'Invalid name, argument, value or control flow in program; inspect recorded code and current RGB') from None
