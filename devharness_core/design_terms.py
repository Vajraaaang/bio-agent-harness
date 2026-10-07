"""Parsed temporal-factor identity; never infer a hypothesis from substrings."""
import ast

from patsy import EvalFactor, ModelDesc, Term


def _is_time_reference(node):
    return ((isinstance(node, ast.Name) and node.id == "numeric_time") or
            (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
             and node.func.id == "Q" and len(node.args) == 1 and not node.keywords
             and isinstance(node.args[0], ast.Constant)
             and node.args[0].value == "numeric_time"))


def uses_temporal_variable(formula):
    """Whether a Patsy formula/term refers to the actual numeric_time field."""
    description = ModelDesc.from_formula(formula)
    return any(_is_time_reference(node)
               for term in description.rhs_termlist for factor in term.factors
               for node in ast.walk(ast.parse(factor.code, mode="eval")))


def specified_temporal_df(formula, default=3):
    """Read a fixed literal temporal df; reject ambiguous declarations."""
    values = set()
    description = ModelDesc.from_formula(formula)
    for term in description.rhs_termlist:
        for factor in term.factors:
            for node in ast.walk(ast.parse(factor.code, mode="eval")):
                if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                        and node.func.id == "cr" and node.args and _is_time_reference(node.args[0])):
                    continue
                declarations = [keyword.value for keyword in node.keywords if keyword.arg == "df"]
                if len(node.args) > 1:
                    declarations.append(node.args[1])
                if len(declarations) > 1:
                    raise ValueError("Temporal spline df was supplied more than once")
                if not declarations:
                    if any(keyword.arg == "knots" for keyword in node.keywords):
                        raise ValueError("Explicit spline_dfs is required for a knot-defined fixed spline")
                    continue
                value = declarations[0]
                if not (isinstance(value, ast.Constant) and type(value.value) is int and value.value > 0):
                    raise ValueError("Temporal spline df must be a positive literal integer")
                values.add(value.value)
    if len(values) > 1:
        raise ValueError("Conflicting temporal spline df declarations")
    return next(iter(values)) if values else default


def temporal_spline_formula(formula, df):
    """Replace time expressions while preserving quoted nuisance identifiers."""
    if not uses_temporal_variable(formula):
        raise ValueError("formula_str must contain numeric_time for the temporal test")

    class ReplaceTime(ast.NodeTransformer):
        def replacement(self):
            return ast.parse(f"cr(numeric_time, df={df}, constraints='center')", mode="eval").body

        def visit_Name(self, node):
            return self.replacement() if _is_time_reference(node) else node

        def visit_Call(self, node):
            if (_is_time_reference(node) or
                (isinstance(node.func, ast.Name) and node.func.id == "cr"
                 and node.args and _is_time_reference(node.args[0]))):
                return self.replacement()
            return self.generic_visit(node)

    description = ModelDesc.from_formula(formula)
    terms = []
    for term in description.rhs_termlist:
        factors = []
        for factor in term.factors:
            tree = ast.fix_missing_locations(ReplaceTime().visit(ast.parse(factor.code, mode="eval")))
            factors.append(EvalFactor(ast.unparse(tree.body)))
        terms.append(Term(factors))
    return ModelDesc(description.lhs_termlist, terms).describe().removeprefix("~ ")
