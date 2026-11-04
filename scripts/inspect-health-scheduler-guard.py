"""Report finite command-loop metadata; keep the private scheduler on its host."""
import ast
import hashlib
import json
from pathlib import Path

path = Path('/opt/signaldesk/src/scheduler.py')
assert path.is_file() and not path.is_symlink()
raw = path.read_bytes()
assert hashlib.sha256(raw).hexdigest() == 'fdaf8763ecdb37d82e3205bf377bec7f1a6dcfcd3b30d19b0078d2c655a9dd81'
tree = ast.parse(raw.decode('utf-8'))
known = {'src.health_news_generator', 'health_news_generator.py'}
sites = [node for node in ast.walk(tree) if isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value in known]
parents = {id(child): node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
items = []
for site in sites:
    ancestors, current = [], site
    while id(current) in parents:
        current = parents[id(current)]
        ancestors.append(current)
    enclosing = next((node for node in ancestors if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))), None)
    assignment = next((node for node in ancestors if isinstance(node, (ast.Assign, ast.AnnAssign))), None)
    names = []
    if isinstance(assignment, ast.Assign):
        names = [target.id for target in assignment.targets if isinstance(target, ast.Name)]
    elif isinstance(assignment, ast.AnnAssign) and isinstance(assignment.target, ast.Name):
        names = [assignment.target.id]
    loops = [node for node in ast.walk(enclosing or tree) if isinstance(node, ast.For) and isinstance(node.target, ast.Name) and ((isinstance(node.iter, ast.Name) and node.iter.id in names) or node.iter is getattr(assignment, 'value', None))]
    items.append({'line': site.lineno, 'inside_function': enclosing is not None,
                  'assigned_command_collection': bool(names), 'direct_named_command_loops': len(loops),
                  'nearest_statement_type': next((type(node).__name__ for node in ancestors if isinstance(node, ast.stmt)), None),
                  'list_or_tuple_container': any(isinstance(node, (ast.List, ast.Tuple)) for node in ancestors)})
print(json.dumps({'source_sha256': hashlib.sha256(raw).hexdigest(), 'known_health_command_sites': items,
                  'private_source_exported': False, 'production_modified': False}))
