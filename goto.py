
import ast


# Or, to avoid messing around with the bytecode, we can go the
# much more complicated route:
# - First, we flatten any Python AST into a series of statements,
#   with even more goto's added for while/for loops, if's, etc.
#   (We can avoid doing this for all sub-ASTs where there are no
#    no goto-labels. The goal is to have all goto-labels at
#    the top-level, not inside a sub-AST.)
# - Only conditional goto's can stay.
# - All Gotos and Goto-labels are marked somehow as special elements.
# So, we end up with sth like:
#   x()
#   y()
#   <goto-label "a">
#   z()
#   <goto-stmnt "a">
#   w()
#   if v(): <goto-stmnt "a">
#   q()
# Now, we can implement the goto-handling based on this flattened code:
# - Add a big endless loop around it. After the final statement,
#   a break would leave the loop.
# - Before the loop, we add the statement `goto = None`.
# - The goto-labels will split the code into multiple part, where
#   we add some `if goto is None:` before each part
#   (excluding the goto-labels).
# - For the goto-labels itself, we add this code:
#   `if goto == <goto-label>: goto = None`
# - For every goto-statement, we add this code:
#   `goto = <goto-stmnt>; continue`


class GotoLabel:
    def __init__(self, label):
        self.label = label


class GotoStatement:
    def __init__(self, label):
        self.label = label


class _Flatten:
    def __init__(self):
        self.c = 1

    def make_jump(self):
        label = self.c
        self.c += 1
        return GotoStatement(label)

    def flatten(self, body, breakJump=None, continueJump=None):
        """
        :type body: list[ast.AST]
        :param breakJump: if we find some ast.Break in a while-loop, add this jump
        :param continueJump: if we find some ast.Continue in a while-loop, add this jump
        :rtype: list[ast.AST]
        """
        r = []
        for s in body:
            if isinstance(s, ast.If):
                a = ast.UnaryOp(op=ast.Not(), operand=s.test)
                goto_final_stmnt = self.make_jump()
                if s.orelse:
                    goto_orelse_stmnt = self.make_jump()
                    r += [ast.If(test=a, body=[goto_orelse_stmnt], orelse=[])]
                else:
                    goto_orelse_stmnt = None
                    r += [ast.If(test=a, body=[goto_final_stmnt], orelse=[])]
                r += self.flatten(s.body, breakJump=breakJump, continueJump=continueJump)
                if s.orelse:
                    r += [goto_final_stmnt]
                    r += [GotoLabel(goto_orelse_stmnt.label)]
                    r += self.flatten(s.orelse, breakJump=breakJump, continueJump=continueJump)
                r += [GotoLabel(goto_final_stmnt.label)]
            elif isinstance(s, ast.While):
                if s.orelse: raise NotImplementedError
                goto_repeat_stmnt = self.make_jump()
                r += [GotoLabel(goto_repeat_stmnt.label)]
                a = ast.UnaryOp(op=ast.Not(), operand=s.test)
                goto_final_stmnt = self.make_jump()
                r += [ast.If(test=a, body=[goto_final_stmnt], orelse=[])]
                # Inside the loop body: `break` jumps past the loop, `continue`
                # jumps back to the top (re-checking the test).  When we recurse
                # into the body we install NEW break/continue jumps so that any
                # outer-loop jumps still in scope are shadowed -- break/continue
                # in C always target the innermost enclosing loop.
                r += self.flatten(s.body, breakJump=goto_final_stmnt, continueJump=goto_repeat_stmnt)
                r += [goto_repeat_stmnt]
                r += [GotoLabel(goto_final_stmnt.label)]
            elif isinstance(s, ast.For):
                if s.orelse: raise NotImplementedError
                # Single-iteration `for _ in (0,):` pseudo-loop emitted by `astForCDoWhile`.
                goto_final_stmnt = self.make_jump()
                r += self.flatten(s.body, breakJump=goto_final_stmnt, continueJump=goto_final_stmnt)
                r += [GotoLabel(goto_final_stmnt.label)]
            elif isinstance(s, ast.Try):
                raise NotImplementedError
            elif isinstance(s, ast.Break):
                assert breakJump, "found break in unexpected scope"
                r += [breakJump]
            elif isinstance(s, ast.Continue):
                assert continueJump, "found continue in unexpected scope"
                r += [continueJump]
            else:
                r += [s]
        return r


def _ast_for_value(v):
    if isinstance(v, (str, int)): return ast.Constant(value=v)
    else: raise NotImplementedError("type (%r) %r" % (type(v), v))


class _HandleGoto:

    def __init__(self, gotoVarName):
        self.gotoVarName = gotoVarName

    def handle_goto_stmnt(self, stmnt):
        assert isinstance(stmnt, GotoStatement)
        a = ast.Assign(
            targets=[ast.Name(id=self.gotoVarName, ctx=ast.Store())],
            value=_ast_for_value(stmnt.label))
        return [a, ast.Continue()]

    def handle_goto_label(self, stmnt):
        assert isinstance(stmnt, GotoLabel)
        reset_ast = ast.Assign(
            targets=[ast.Name(id=self.gotoVarName, ctx=ast.Store())],
            value=ast.Name(id="None", ctx=ast.Load()))
        test_ast = ast.Compare(
            left=ast.Name(id=self.gotoVarName, ctx=ast.Store()),
            ops=[ast.Eq()],
            comparators=[_ast_for_value(stmnt.label)])
        return [ast.If(test=test_ast, body=[reset_ast], orelse=[])]

    def handle_body(self, body):
        """
        :type body: list[ast.AST]
        :rtype: list[ast.AST]
        """
        parts = [[]]
        for s in body:
            if isinstance(s, GotoLabel):
                parts += [s, []]
            else:
                parts[-1].append(s)
        r = []
        for l in parts:
            if not l: continue
            if isinstance(l, GotoLabel):
                r += self.handle_goto_label(l)
            else:
                sr = []
                for s in l:
                    if isinstance(s, ast.If):
                        assert not s.orelse
                        assert len(s.body) == 1
                        assert isinstance(s.body[0], GotoStatement)
                        sr += [ast.If(test=s.test, orelse=[],
                                      body=self.handle_goto_stmnt(s.body[0]))]
                    elif isinstance(s, (ast.While, ast.For)):
                        assert False, "not expected: %r" % s
                    elif isinstance(s, GotoStatement):
                        sr += self.handle_goto_stmnt(s)
                    else:
                        sr += [s]
                test_ast = ast.Compare(
                    left=ast.Name(id=self.gotoVarName, ctx=ast.Store()),
                    ops=[ast.Is()],
                    comparators=[ast.Name(id="None", ctx=ast.Load())])
                r += [ast.If(test=test_ast, body=sr, orelse=[])]
        return r

    def wrap_func_body(self, flat_body):
        var_ast = ast.Assign(
            targets=[ast.Name(id=self.gotoVarName, ctx=ast.Store())],
            value=ast.Name(id="None", ctx=ast.Load()))
        main_loop_ast = ast.While(
            test=ast.Name(id="True", ctx=ast.Load()),
            body=self.handle_body(flat_body), orelse=[])
        main_loop_ast.body += [ast.Break()]
        return [var_ast, main_loop_ast]


def transform_goto(f, gotoVarName):
    assert isinstance(f, ast.FunctionDef)
    flat_body = _Flatten().flatten(f.body)
    new_body = _HandleGoto(gotoVarName).wrap_func_body(flat_body)
    new_func_ast = ast.FunctionDef(
        name=f.name,
        args=f.args,
        decorator_list=f.decorator_list,
        body=new_body)
    return new_func_ast
