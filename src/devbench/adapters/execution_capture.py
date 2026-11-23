"""Recognize a bounded, unconditional shell execution without evaluating text."""
from functools import lru_cache
from pathlib import Path
import shlex

from tree_sitter import Language, Parser
import tree_sitter_bash

SHELLS = frozenset('/bin/' + name for name in ('sh', 'bash', 'zsh')) | frozenset(
    '/usr/bin/' + name for name in ('sh', 'bash', 'zsh'))
MAX_COMMAND_CHARS = 16_000


@lru_cache(maxsize=1)
def _language():
    return Language(tree_sitter_bash.language())


def _text(node, source):
    return source[node.start_byte:node.end_byte].decode('utf-8')


def _literal(node, source):
    stack = [node]
    while stack:
        child = stack.pop()
        if child.type not in {'word', 'number', 'raw_string', 'string', 'concatenation',
                              'command_name', 'string_content', 'escape_sequence'}:
            raise ValueError('Nonliteral shell word')
        stack.extend(child.named_children)
    text = _text(node, source)
    if node.type == 'word' and any(char in text for char in '*?[]~'):
        raise ValueError('Shell expansion is not a literal path')
    parts = shlex.split(text)
    if len(parts) != 1:
        raise ValueError('Ambiguous shell word')
    return parts[0]


def _argv(command, source):
    if command.type != 'command':
        raise ValueError('Not a simple command')
    # '-' can be an unnamed token in this grammar. Tokenize the command span
    # only after its AST has excluded substitutions, assignments and redirects.
    for child in command.named_children:
        _literal(child, source)
    return shlex.split(_text(command, source))


def _commands(node):
    if node.type not in {'program', 'list'}:
        return [node]
    result = []
    for child in node.children:
        if child.type == 'comment':
            continue
        if not child.is_named:
            if child.type not in {';', '&&'}:
                raise ValueError('Conditional, pipeline or background execution')
            continue
        result.extend(_commands(child))
    return result


def _path(value, workspace):
    path = Path(value)
    return path if path.is_absolute() else workspace / path


def _python(argv, workspace, python):
    return bool(argv) and _path(argv[0], workspace).absolute() == python.absolute()


def _direct(argv, workspace, python):
    return (_python(argv, workspace, python) and len(argv) == 2
            and _path(argv[1], workspace).resolve() == workspace / 'solve.py')


def _prefix(node, source, workspace, python):
    redirects = []
    command = node
    if node.type == 'redirected_statement':
        command = node.child_by_field_name('body')
        redirects = [child for child in node.named_children if child != command]
    argv = _argv(command, source)
    if redirects:
        # The Bash grammar may omit a standalone '-' argument before a
        # heredoc from the command node. Validate that complete header too.
        header = source[command.start_byte:min(n.start_byte for n in redirects)].decode()
        argv = shlex.split(header)
    if not argv:
        return False
    if argv[0] == 'cd':
        return not redirects and len(argv) == 2 and _path(argv[1], workspace).resolve() == workspace
    if argv[0] in {'sed', '/bin/sed', '/usr/bin/sed'}:
        return (not redirects and len(argv) >= 3 and '-i' in argv[1:-1]
                and _path(argv[-1], workspace).resolve() == workspace / 'solve.py')
    is_cat = argv[0] in {'cat', '/bin/cat', '/usr/bin/cat'} and len(argv) == 1
    is_python = _python(argv, workspace, python) and argv[1:] in (['-'],)
    if not (is_cat or is_python):
        return False
    heredoc, destinations = False, []
    stack = list(redirects)
    while stack:
        redirect = stack.pop()
        if redirect.type == 'heredoc_redirect':
            start = next((n for n in redirect.named_children if n.type == 'heredoc_start'), None)
            end = next((n for n in redirect.named_children if n.type == 'heredoc_end'), None)
            allowed = {'heredoc_start', 'heredoc_body', 'heredoc_end', 'file_redirect'}
            if (start is None or end is None or redirect.children[0].type != '<<'
                    or any(n.type not in allowed for n in redirect.named_children)
                    or any(n.type != '<<' for n in redirect.children if not n.is_named)):
                return False
            delimiter = _text(start, source)
            if (len(delimiter) < 3 or delimiter[0] not in "'\"" or delimiter[-1] != delimiter[0]
                    or delimiter[0] in delimiter[1:-1] or '\\' in delimiter[1:-1]):
                return False
            line_start = source.rfind(b'\n', 0, end.start_byte) + 1
            line_end = source.find(b'\n', end.end_byte)
            if line_end < 0:
                line_end = len(source)
            if source[line_start:line_end] != delimiter[1:-1].encode():
                return False
            heredoc = True
            stack.extend(n for n in redirect.named_children if n.type == 'file_redirect')
        elif redirect.type == 'file_redirect':
            destination = redirect.child_by_field_name('destination')
            if destination is None or redirect.children[0].type not in {'>', '>>'}:
                return False
            destinations.append(_path(_literal(destination, source), workspace).resolve())
        else:
            return False
    if is_cat:
        return heredoc and destinations == [workspace / 'solve.py']
    return heredoc and not destinations


def script_invocation(command, workspace: Path, python: Path) -> bool:
    """A successful enclosing command proves the final direct invocation ran.

    Supports real heredoc writes, relative targets and sed/Python edit-and-run
    sequences. Parent-shell control flow and failure masking remain unprovable.
    This recognizes the supported POSIX shell subset, not arbitrary zsh code.
    """
    if not isinstance(command, str) or len(command) > MAX_COMMAND_CHARS:
        return False
    try:
        try:
            words = shlex.split(command)
        except ValueError:
            words = []  # Quoted heredoc bodies need not be valid shell words.
        if len(words) == 3 and words[0] in SHELLS and words[1] in {'-c', '-lc'}:
            command = words[2]
        source = command.encode('utf-8')
        root = Parser(_language()).parse(source).root_node
        if root.has_error:
            return False
        commands = _commands(root)
        return (bool(commands) and _direct(_argv(commands[-1], source), workspace, python)
                and all(_prefix(node, source, workspace, python) for node in commands[:-1]))
    except (ValueError, OSError, UnicodeError, RecursionError):
        return False
