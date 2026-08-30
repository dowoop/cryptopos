"""Measure all three sale-data access surfaces from source, offline.

    python3 tools/api_surface.py

This is the cold-checkout half of the ownership evidence. ``isolation_probe.py``
asks a live database whether a real second user can read an existing sale; this
tool asks whether the endpoint, DocType list and report surfaces even try to
stop them. Both are wanted. The live probe needs a bench and a disposable user,
while this gate imports neither Frappe nor the application and never opens a
socket.

The report is deliberately a measurement, not an ownership model. It does not
decide whether visitors share one shop or receive a shop each, and it does not
write checks into the API. It names the caller-addressable records and global
financial views that decision must account for, then fails while they have no
caller constraint.

``UNKNOWN`` is a failing answer. D35 records the defect this buys against: a
guard whose heading is wider than what it actually audited reported PASS while
bad data sat outside its matcher. The in-memory negative controls below
therefore drive both sides of every detector on every run.
"""

import ast
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

YES = "YES"
NO = "NO"
UNKNOWN = "UNKNOWN"

NAMED_RECORD = "NAMED_RECORD"
INSTANCE_AGGREGATE = "INSTANCE_AGGREGATE"
NEITHER = "NEITHER"

API_PATH = Path(__file__).resolve().parent.parent / "cryptopos" / "api.py"
APP_PATH = API_PATH.parent
METADATA_PATH = APP_PATH / "cryptopos"
HOOKS_PATH = APP_PATH / "hooks.py"
SALE_DOCTYPE = "Crypto Sale"

PERMISSION_FLAGS = (
    "select",
    "read",
    "write",
    "create",
    "delete",
    "submit",
    "cancel",
    "amend",
    "report",
    "export",
    "import",
    "share",
    "print",
    "email",
)

# These are vocabulary, not endpoint names. Classification follows identifiers,
# calls, filters and return-shaping operations in the source. A typed inventory
# would be correct only until the twelfth endpoint landed -- the closed-world
# guard failure D35 warns about.
RECORD_QUERY_CALLS = {
    "frappe.db.get_value",
    "frappe.get_cached_doc",
    "frappe.get_doc",
    "frappe.get_value",
}
COLLECTION_QUERY_CALLS = {
    "frappe.db.get_all",
    "frappe.db.get_list",
    "frappe.db.sql",
    "frappe.get_all",
    "frappe.get_list",
}
PERMISSION_FILTERED_CALLS = {"frappe.db.get_list", "frappe.get_list"}
FINANCIAL_WORDS = {
    "amount",
    "balance",
    "cent",
    "cents",
    "credit",
    "credited",
    "currency",
    "invoice",
    "ledger",
    "money",
    "payment",
    "payments",
    "price",
    "rate",
    "takings",
    "transaction",
    "tx",
    "usd",
}


@dataclass
class FunctionFacts:
    """Facts derived from one function before the endpoint report is rendered."""

    node: ast.FunctionDef | ast.AsyncFunctionDef
    parameters: tuple[str, ...]
    named_parameters: set[str] = field(default_factory=set)
    aggregate: bool = False
    financial: bool = False
    sale_data: bool = False
    owner: str = NO
    local_calls: list[ast.Call] = field(default_factory=list)


@dataclass(frozen=True)
class Endpoint:
    name: str
    line: int
    allow_guest: str
    roles: tuple[str, ...]
    allowed_roles: tuple[str, ...]
    roles_unknown: bool
    owner: str
    exposure: str
    financial: bool
    sale_data: bool

    @property
    def blocker(self):
        if self.owner == UNKNOWN:
            return "owner constraint is UNKNOWN"
        if self.owner == YES:
            return None
        if self.exposure == NAMED_RECORD:
            return "caller-supplied record has no owner constraint"
        if self.exposure == INSTANCE_AGGREGATE and self.financial:
            return "instance-wide financial aggregate has no owner constraint"
        return None


@dataclass(frozen=True)
class PermissionBlock:
    role: str
    flags: tuple[str, ...]
    if_owner: str

    @property
    def reads_rows(self):
        return "read" in self.flags or "report" in self.flags


@dataclass(frozen=True)
class DocTypeSurface:
    name: str
    path: Path
    child_table: bool
    permissions: tuple[PermissionBlock, ...]
    permission_query: str

    @property
    def blockers(self):
        if self.name != SALE_DOCTYPE or self.child_table:
            return ()
        blockers = []
        for permission in self.permissions:
            if not permission.reads_rows or permission.if_owner == YES:
                continue
            if self.permission_query == YES:
                continue
            if permission.if_owner == UNKNOWN:
                reason = "if_owner is UNKNOWN"
            elif self.permission_query == UNKNOWN:
                reason = "permission query is UNKNOWN"
            else:
                reason = "read/report has neither if_owner nor a permission query"
            blockers.append((permission.role, reason))
        return tuple(blockers)


@dataclass(frozen=True)
class ReportSurface:
    name: str
    path: Path
    ref_doctype: str
    roles: tuple[str, ...]
    row_permissions: str

    @property
    def blockers(self):
        if self.ref_doctype != SALE_DOCTYPE or self.row_permissions == YES:
            return ()
        reason = (
            "report query bypasses row permissions"
            if self.row_permissions == NO
            else "report row-permission behavior is UNKNOWN"
        )
        return tuple((role, reason) for role in self.roles)


@dataclass(frozen=True)
class WorkspaceSurface:
    name: str
    path: Path
    public: str
    roles: tuple[str, ...]
    number_cards: tuple[str, ...]

    @property
    def role_restriction(self):
        if self.roles or self.public == NO:
            return YES
        return NO if self.public == YES else UNKNOWN


@dataclass(frozen=True)
class CardSurface:
    name: str
    path: Path
    document_type: str
    method: str
    public: str
    workspaces: tuple[WorkspaceSurface, ...]

    @property
    def blocker(self):
        if self.document_type != SALE_DOCTYPE or self.public == NO:
            return None
        if self.public == UNKNOWN:
            return "sale-data card publicity is UNKNOWN"
        if not self.workspaces:
            return "public sale-data card has no containing workspace gate"
        open_workspaces = [
            workspace.name
            for workspace in self.workspaces
            if workspace.role_restriction != YES
        ]
        if open_workspaces:
            return "public sale-data card is carried by workspace(s) without a role restriction: " + ", ".join(
                open_workspaces
            )
        return None


def call_name(node):
    """Return a dotted call/attribute name when the syntax makes it knowable."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parent = call_name(node.value)
        return f"{parent}.{node.attr}" if parent else node.attr
    return None


def body_nodes(function):
    """Walk a function body without laundering facts out of a nested function."""
    stack = list(reversed(function.body))
    while stack:
        node = stack.pop()
        yield node
        children = []
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
                continue
            children.append(child)
        stack.extend(reversed(children))


def parameter_names(function):
    arguments = function.args
    positional = [*arguments.posonlyargs, *arguments.args]
    return tuple(argument.arg for argument in [*positional, *arguments.kwonlyargs])


def parameter_references(node, parameters):
    return {
        child.id
        for child in ast.walk(node)
        if isinstance(child, ast.Name) and child.id in parameters
    }


def looks_like_record_identifier(name):
    return name == "name" or name.endswith(("_name", "_id", "_ref"))


def dictionary_pairs(node):
    if not isinstance(node, ast.Dict):
        return []
    return [
        (key.value, value)
        for key, value in zip(node.keys, node.values, strict=True)
        if isinstance(key, ast.Constant) and isinstance(key.value, str)
    ]


def filter_nodes(call):
    found = [keyword.value for keyword in call.keywords if keyword.arg in {"filters", "or_filters"}]
    if len(call.args) > 1 and isinstance(call.args[1], ast.Dict):
        found.append(call.args[1])
    return found


def direct_named_parameters(function, nodes):
    parameters = set(parameter_names(function))
    found = set()
    for node in nodes:
        if not isinstance(node, ast.Call):
            continue
        name = call_name(node.func)
        if name in RECORD_QUERY_CALLS:
            candidates = list(node.args[1:2])
            candidates.extend(
                keyword.value for keyword in node.keywords if keyword.arg in {"name", "docname"}
            )
            for candidate in candidates:
                found.update(parameter_references(candidate, parameters))

        if name not in COLLECTION_QUERY_CALLS:
            continue
        for filters in filter_nodes(node):
            for key, value in dictionary_pairs(filters):
                references = parameter_references(value, parameters)
                if key == "name" or key.endswith(("_name", "_id", "_ref")):
                    found.update(references)
                found.update(reference for reference in references if looks_like_record_identifier(reference))
    return found


def assigned_call_names(nodes):
    assigned = set()
    for node in nodes:
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        value = node.value
        if not isinstance(value, ast.Call):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        for target in targets:
            if isinstance(target, ast.Name):
                assigned.add(target.id)
    return assigned


def directly_aggregates(nodes):
    """Find broad queries or call results actually treated as collections."""
    if any(
        isinstance(node, ast.Call) and call_name(node.func) in COLLECTION_QUERY_CALLS
        for node in nodes
    ):
        return True

    # ``settle.unbooked()`` and ``reconcile.late_payments()`` are intentionally
    # not named here. The body proves their collection shape by taking len/sum
    # over the returned rows. This keeps classification attached to code shape,
    # rather than a list that quietly stops auditing after a rename.
    candidates = assigned_call_names(nodes)
    for node in nodes:
        if isinstance(node, ast.Call) and call_name(node.func) in {"len", "sum"}:
            if any(parameter_references(argument, candidates) for argument in node.args):
                return True
    return False


def words_in_function(function, nodes):
    words = set(re.findall(r"[a-z0-9]+", function.name.lower()))
    for node in nodes:
        value = None
        if isinstance(node, ast.Name):
            value = node.id
        elif isinstance(node, ast.Attribute):
            value = node.attr
        elif isinstance(node, ast.keyword) and node.arg:
            value = node.arg
        if value:
            words.update(re.findall(r"[a-z0-9]+", value.lower()))
        if isinstance(node, ast.Dict):
            for key, _value in dictionary_pairs(node):
                words.update(re.findall(r"[a-z0-9]+", key.lower()))
    return words


def directly_reaches_sale_data(function, nodes, aggregate, financial):
    if aggregate and financial:
        return True
    if any("sale" in name.lower() for name in parameter_names(function)):
        return True
    return any(
        isinstance(node, ast.Constant) and node.value == SALE_DOCTYPE
        for node in nodes
    )


def direct_caller_reference(node):
    name = call_name(node)
    if name in {"frappe.local.session.user", "frappe.session.user"}:
        return True
    return isinstance(node, ast.Call) and call_name(node.func) == "frappe.get_user"


def caller_aliases(nodes):
    aliases = set()
    changed = True
    while changed:
        changed = False
        for node in nodes:
            if not isinstance(node, (ast.Assign, ast.AnnAssign)):
                continue
            value = node.value
            if not contains_caller(value, aliases):
                continue
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if isinstance(target, ast.Name) and target.id not in aliases:
                    aliases.add(target.id)
                    changed = True
    return aliases


def contains_caller(node, aliases):
    return any(
        direct_caller_reference(child)
        or (isinstance(child, ast.Name) and child.id in aliases)
        for child in ast.walk(node)
    )


def contains_owner_word(node):
    for child in ast.walk(node):
        name = None
        if isinstance(child, ast.Name):
            name = child.id
        elif isinstance(child, ast.Attribute):
            name = child.attr
        elif isinstance(child, ast.Constant) and isinstance(child.value, str):
            name = child.value
        if name and set(re.findall(r"[a-z0-9]+", name.lower())) & {"owner", "user"}:
            return True
    return False


def suite_stops(statements):
    """Recognise branches which refuse or stop returning the unrestricted path."""
    for statement in statements:
        if isinstance(statement, (ast.Raise, ast.Return)):
            return True
        if isinstance(statement, ast.Expr) and isinstance(statement.value, ast.Call):
            name = call_name(statement.value.func) or ""
            if name in {"frappe.abort", "frappe.throw"} or name.endswith(".check_permission"):
                return True
        if isinstance(statement, ast.If) and statement.orelse:
            if suite_stops(statement.body) and suite_stops(statement.orelse):
                return True
    return False


def owner_filter(call, aliases):
    candidates = filter_nodes(call)
    candidates.extend(
        ast.Dict(keys=[ast.Constant(keyword.arg)], values=[keyword.value])
        for keyword in call.keywords
        if keyword.arg in {"owner", "user"}
    )
    return any(
        key in {"owner", "user"} and contains_caller(value, aliases)
        for candidate in candidates
        for key, value in dictionary_pairs(candidate)
    )


def direct_owner_verdict(nodes):
    aliases = caller_aliases(nodes)
    caller_seen = any(contains_caller(node, aliases) for node in nodes)
    permission_seen = False

    for node in nodes:
        if isinstance(node, ast.Call):
            name = call_name(node.func) or ""
            if name in PERMISSION_FILTERED_CALLS or name.endswith(".check_permission"):
                return YES
            if owner_filter(node, aliases):
                return YES
            if "permission" in name or any(word in name.lower() for word in ("ensure_owner", "require_owner")):
                permission_seen = True

        if isinstance(node, ast.If):
            test_has_guard = (
                contains_caller(node.test, aliases) and contains_owner_word(node.test)
            ) or any(
                isinstance(child, ast.Call) and "permission" in (call_name(child.func) or "")
                for child in ast.walk(node.test)
            )
            if test_has_guard and (suite_stops(node.body) or suite_stops(node.orelse)):
                return YES

    # Mentioning the caller or calling a permission-looking helper is evidence
    # of an attempted check, but not evidence it constrains a result. Calling it
    # PASS would repeat idcheck's discarded-UNCHECKED defect from D35.
    return UNKNOWN if caller_seen or permission_seen else NO


def whitelist_decorator(function):
    for decorator in function.decorator_list:
        target = decorator.func if isinstance(decorator, ast.Call) else decorator
        if call_name(target) == "frappe.whitelist":
            return decorator
    return None


def allow_guest_verdict(decorator):
    if not isinstance(decorator, ast.Call):
        return NO
    values = [keyword.value for keyword in decorator.keywords if keyword.arg == "allow_guest"]
    if not values and decorator.args:
        values = decorator.args[:1]
    if not values:
        return NO
    try:
        value = ast.literal_eval(values[0])
    except (ValueError, TypeError):
        return UNKNOWN
    return YES if value is True else NO if value is False else UNKNOWN


def exact_roles(function, source):
    roles = []
    literal_scopes = []
    unknown = False
    for node in body_nodes(function):
        if not isinstance(node, ast.Call) or call_name(node.func) != "frappe.only_for":
            continue
        values = list(node.args[:1])
        values.extend(keyword.value for keyword in node.keywords if keyword.arg in {"role", "roles"})
        if not values:
            roles.append("UNKNOWN (frappe.only_for called without roles)")
            unknown = True
            continue
        segment = ast.get_source_segment(source, values[0])
        roles.append(" ".join(segment.split()) if segment else "UNKNOWN (source unavailable)")
        try:
            value = ast.literal_eval(values[0])
        except (ValueError, TypeError):
            unknown = True
            continue
        if isinstance(value, str):
            literal_scopes.append({value})
        elif isinstance(value, (list, tuple, set)) and all(
            isinstance(role, str) for role in value
        ):
            literal_scopes.append(set(value))
        else:
            unknown = True

    allowed = set.intersection(*literal_scopes) if literal_scopes else set()
    return tuple(roles), tuple(sorted(allowed)), unknown


def local_call_target(call, functions):
    return call.func.id if isinstance(call.func, ast.Name) and call.func.id in functions else None


def map_named_callers(fact, callee, call):
    mapped = set()
    for index, parameter in enumerate(callee.parameters):
        if parameter not in callee.named_parameters:
            continue
        values = list(call.args[index : index + 1])
        values.extend(keyword.value for keyword in call.keywords if keyword.arg == parameter)
        for value in values:
            mapped.update(parameter_references(value, set(fact.parameters)))
    return mapped


def analyze_source(source, filename="<memory>"):
    tree = ast.parse(source, filename=filename)
    functions = {
        node.name: node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    facts = {}
    for name, function in functions.items():
        nodes = list(body_nodes(function))
        aggregate = directly_aggregates(nodes)
        financial = bool(words_in_function(function, nodes) & FINANCIAL_WORDS)
        facts[name] = FunctionFacts(
            node=function,
            parameters=parameter_names(function),
            named_parameters=direct_named_parameters(function, nodes),
            aggregate=aggregate,
            financial=financial,
            sale_data=directly_reaches_sale_data(function, nodes, aggregate, financial),
            owner=direct_owner_verdict(nodes),
            local_calls=[node for node in nodes if isinstance(node, ast.Call)],
        )

    # Local wrappers are part of the endpoint body in practice. Propagating the
    # facts makes poll follow status and the number-card wrappers follow
    # unbooked, without spelling any of those endpoint names into the checker.
    changed = True
    while changed:
        changed = False
        for fact in facts.values():
            for call in fact.local_calls:
                target = local_call_target(call, functions)
                if not target:
                    continue
                callee = facts[target]
                mapped = map_named_callers(fact, callee, call)
                if not mapped.issubset(fact.named_parameters):
                    fact.named_parameters.update(mapped)
                    changed = True
                if callee.aggregate and not fact.aggregate:
                    fact.aggregate = True
                    changed = True
                if callee.financial and not fact.financial:
                    fact.financial = True
                    changed = True
                if callee.sale_data and not fact.sale_data:
                    fact.sale_data = True
                    changed = True
                inherited_owner = callee.owner
                if inherited_owner == YES and fact.owner != YES:
                    fact.owner = YES
                    changed = True
                elif inherited_owner == UNKNOWN and fact.owner == NO:
                    fact.owner = UNKNOWN
                    changed = True

    endpoints = []
    for function in functions.values():
        decorator = whitelist_decorator(function)
        if decorator is None:
            continue
        fact = facts[function.name]
        exposure = NAMED_RECORD if fact.named_parameters else INSTANCE_AGGREGATE if fact.aggregate else NEITHER
        roles, allowed_roles, roles_unknown = exact_roles(function, source)
        endpoints.append(
            Endpoint(
                name=function.name,
                line=function.lineno,
                allow_guest=allow_guest_verdict(decorator),
                roles=roles,
                allowed_roles=allowed_roles,
                roles_unknown=roles_unknown,
                owner=fact.owner,
                exposure=exposure,
                financial=fact.financial,
                sale_data=fact.sale_data,
            )
        )
    return endpoints


def role_text(endpoint):
    return "; ".join(endpoint.roles) if endpoint.roles else "NONE"


def exposure_text(endpoint):
    if endpoint.exposure == INSTANCE_AGGREGATE:
        kind = "financial" if endpoint.financial else "non-financial"
        return f"{endpoint.exposure} ({kind})"
    return endpoint.exposure


def yes_no_unknown(value):
    if value is True or value == 1:
        return YES
    if value is False or value == 0 or value is None:
        return NO
    return UNKNOWN


def mapping_names(source, variable, filename="<memory>"):
    """Read the keys of one active top-level hooks mapping."""
    tree = ast.parse(source, filename=filename)
    assignment = None
    for node in tree.body:
        targets = []
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target]
        if any(isinstance(target, ast.Name) and target.id == variable for target in targets):
            assignment = node.value
    if assignment is None:
        return set(), True
    try:
        value = ast.literal_eval(assignment)
    except (ValueError, TypeError):
        return set(), False
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        return set(), False
    return set(value), True


def permission_block(data):
    role = data.get("role")
    if not isinstance(role, str) or not role:
        role = "UNKNOWN"
    flags = tuple(flag for flag in PERMISSION_FLAGS if yes_no_unknown(data.get(flag)) == YES)
    return PermissionBlock(
        role=role,
        flags=flags,
        if_owner=yes_no_unknown(data.get("if_owner")),
    )


def analyze_doctype(data, path, permission_queries, queries_known=True):
    name = data.get("name")
    if not isinstance(name, str) or not name:
        raise ValueError(f"{path}: DocType has no string name")
    raw_permissions = data.get("permissions", [])
    if not isinstance(raw_permissions, list) or not all(
        isinstance(permission, dict) for permission in raw_permissions
    ):
        raise ValueError(f"{path}: permissions is not a list of objects")
    permission_query = YES if name in permission_queries else NO if queries_known else UNKNOWN
    return DocTypeSurface(
        name=name,
        path=path,
        child_table=yes_no_unknown(data.get("istable")) == YES,
        permissions=tuple(permission_block(permission) for permission in raw_permissions),
        permission_query=permission_query,
    )


def report_roles(data):
    roles = []
    raw_roles = data.get("roles", [])
    if not isinstance(raw_roles, list):
        return ("UNKNOWN",)
    for item in raw_roles:
        role = item.get("role") if isinstance(item, dict) else item
        roles.append(role if isinstance(role, str) and role else "UNKNOWN")
    return tuple(roles)


def literal_first_argument(call):
    if not call.args:
        return None
    value = call.args[0]
    return value.value if isinstance(value, ast.Constant) and isinstance(value.value, str) else None


def report_row_permissions(source, ref_doctype, filename="<memory>"):
    """Tell whether every visible query of the report's DocType respects rows."""
    tree = ast.parse(source, filename=filename)
    nodes = list(ast.walk(tree))
    aliases = caller_aliases(nodes)
    permission_filtered = False
    unknown_query = False
    for node in nodes:
        if not isinstance(node, ast.Call):
            continue
        name = call_name(node.func)
        if name in COLLECTION_QUERY_CALLS and literal_first_argument(node) == ref_doctype:
            if name in PERMISSION_FILTERED_CALLS or owner_filter(node, aliases):
                permission_filtered = True
            else:
                return NO
        if name == "frappe.db.sql" and any(
            isinstance(child, ast.Constant)
            and isinstance(child.value, str)
            and ref_doctype in child.value
            for child in ast.walk(node)
        ):
            unknown_query = True
    if unknown_query:
        return UNKNOWN
    return YES if permission_filtered else UNKNOWN


def analyze_report(data, path, source):
    name = data.get("name")
    ref_doctype = data.get("ref_doctype")
    if not isinstance(name, str) or not name:
        raise ValueError(f"{path}: Report has no string name")
    if not isinstance(ref_doctype, str):
        ref_doctype = "UNKNOWN"
    row_permissions = report_row_permissions(source, ref_doctype, str(path.with_suffix(".py")))
    return ReportSurface(
        name=name,
        path=path,
        ref_doctype=ref_doctype,
        roles=report_roles(data),
        row_permissions=row_permissions,
    )


def analyze_workspace(data, path):
    name = data.get("name")
    if not isinstance(name, str) or not name:
        raise ValueError(f"{path}: Workspace has no string name")
    cards = []
    for item in data.get("number_cards", []):
        if isinstance(item, dict) and isinstance(item.get("number_card_name"), str):
            cards.append(item["number_card_name"])
    return WorkspaceSurface(
        name=name,
        path=path,
        public=yes_no_unknown(data.get("public")),
        roles=report_roles(data),
        number_cards=tuple(cards),
    )


def analyze_card(data, path, workspaces):
    name = data.get("name")
    if not isinstance(name, str) or not name:
        raise ValueError(f"{path}: Number Card has no string name")
    document_type = data.get("document_type")
    method = data.get("method")
    return CardSurface(
        name=name,
        path=path,
        document_type=document_type if isinstance(document_type, str) else "UNKNOWN",
        method=method if isinstance(method, str) else "NONE",
        public=yes_no_unknown(data.get("is_public")),
        workspaces=tuple(
            workspace for workspace in workspaces if name in workspace.number_cards
        ),
    )


def json_documents(root):
    documents = []
    for path in sorted(root.rglob("*.json")):
        with path.open(encoding="utf-8") as stream:
            data = json.load(stream)
        if not isinstance(data, dict):
            raise ValueError(f"{path}: top-level JSON value is not an object")
        documents.append((path, data))
    return documents


def scan_metadata(documents, hooks_source):
    permission_queries, queries_known = mapping_names(
        hooks_source,
        "permission_query_conditions",
        str(HOOKS_PATH),
    )
    doctypes = tuple(
        analyze_doctype(data, path, permission_queries, queries_known)
        for path, data in documents
        if data.get("doctype") == "DocType"
    )
    workspaces = tuple(
        analyze_workspace(data, path)
        for path, data in documents
        if data.get("doctype") == "Workspace"
    )
    reports = []
    for path, data in documents:
        if data.get("doctype") != "Report":
            continue
        script_path = path.with_suffix(".py")
        source = script_path.read_text(encoding="utf-8") if script_path.exists() else ""
        reports.append(analyze_report(data, path, source))
    cards = tuple(
        analyze_card(data, path, workspaces)
        for path, data in documents
        if data.get("doctype") == "Number Card"
    )
    return doctypes, tuple(reports), cards, workspaces


def negative_control():
    source = '''
import frappe

@frappe.whitelist()
def guarded(sale_name):
    sale = frappe.get_doc("Crypto Sale", sale_name)
    if sale.owner != frappe.session.user:
        frappe.throw("not your sale", frappe.PermissionError)
    return sale.name

@frappe.whitelist()
def unguarded(sale_name):
    sale = frappe.get_doc("Crypto Sale", sale_name)
    return sale.name
'''
    endpoints = {endpoint.name: endpoint for endpoint in analyze_source(source)}
    checks = [
        ("guarded", endpoints["guarded"].owner == YES and endpoints["guarded"].blocker is None),
        ("unguarded", endpoints["unguarded"].owner == NO and endpoints["unguarded"].blocker is not None),
    ]
    print("endpoint controls — constructed in memory, parsed by the production detector")
    for name, passed in checks:
        endpoint = endpoints[name]
        flagged = endpoint.blocker is not None
        print(
            f"  {'PASS' if passed else 'FAIL'}  {name:<9} "
            f"owner={endpoint.owner:<7} flagged={'YES' if flagged else 'NO'}"
        )
    print(f"  {sum(passed for _name, passed in checks)}/{len(checks)} controls passed")
    return all(passed for _name, passed in checks)


def metadata_controls():
    unrestricted = {
        "doctype": "DocType",
        "name": SALE_DOCTYPE,
        "permissions": [{"role": "Sales User", "read": 1, "report": 1}],
    }
    owner_scoped = {
        **unrestricted,
        "permissions": [{"role": "Sales User", "read": 1, "if_owner": 1}],
    }
    memory_path = Path("<memory>")
    broad_doctype = analyze_doctype(unrestricted, memory_path, set())
    owned_doctype = analyze_doctype(owner_scoped, memory_path, set())
    queried_doctype = analyze_doctype(unrestricted, memory_path, {SALE_DOCTYPE})

    report_data = {
        "doctype": "Report",
        "name": "Memory Takings",
        "ref_doctype": SALE_DOCTYPE,
        "roles": [{"role": "Sales User"}],
    }
    bypass_report = analyze_report(
        report_data,
        memory_path,
        'import frappe\nfrappe.get_all("Crypto Sale", fields=["name"])\n',
    )
    filtered_report = analyze_report(
        report_data,
        memory_path,
        'import frappe\nfrappe.get_list("Crypto Sale", fields=["name"])\n',
    )

    open_workspace = analyze_workspace(
        {
            "name": "Open",
            "public": 1,
            "roles": [],
            "number_cards": [{"number_card_name": "Memory Card"}],
        },
        memory_path,
    )
    gated_workspace = analyze_workspace(
        {
            "name": "Gated",
            "public": 1,
            "roles": [{"role": "System Manager"}],
            "number_cards": [{"number_card_name": "Memory Card"}],
        },
        memory_path,
    )
    public_card_data = {
        "name": "Memory Card",
        "document_type": SALE_DOCTYPE,
        "method": "example.value",
        "is_public": 1,
    }
    private_card_data = {**public_card_data, "is_public": 0}
    public_open_card = analyze_card(public_card_data, memory_path, (open_workspace,))
    private_open_card = analyze_card(private_card_data, memory_path, (open_workspace,))
    public_gated_card = analyze_card(public_card_data, memory_path, (gated_workspace,))

    active_queries, active_known = mapping_names(
        '# permission_query_conditions = {"Ignored": "x"}\n'
        'permission_query_conditions = {"Crypto Sale": "scope"}\n',
        "permission_query_conditions",
    )
    absent_queries, absent_known = mapping_names(
        '# permission_query_conditions = {"Crypto Sale": "scope"}\n',
        "permission_query_conditions",
    )

    checks = [
        ("doctype broad", bool(broad_doctype.blockers), "flagged"),
        ("doctype if_owner", not owned_doctype.blockers, "clear"),
        ("doctype query", not queried_doctype.blockers, "clear"),
        ("report get_all", bool(bypass_report.blockers), "flagged"),
        ("report get_list", not filtered_report.blockers, "clear"),
        ("card public/open", public_open_card.blocker is not None, "flagged"),
        ("card private", private_open_card.blocker is None, "clear"),
        ("card role-gated", public_gated_card.blocker is None, "clear"),
        (
            "hook active",
            active_known and active_queries == {SALE_DOCTYPE},
            "detected",
        ),
        ("hook commented", absent_known and not absent_queries, "ignored"),
    ]
    print("\nmetadata controls — constructed in memory, parsed by the production detectors")
    for name, passed, expected in checks:
        print(f"  {'PASS' if passed else 'FAIL'}  {name:<18} expected={expected}")
    print(f"  {sum(passed for _name, passed, _expected in checks)}/{len(checks)} controls passed")
    return all(passed for _name, passed, _expected in checks)


def report(endpoints, path):
    print(f"\napi surface — {path}")
    print("  AST only: no application import, bench, database or socket")
    for endpoint in endpoints:
        print(f"\n  {endpoint.name} (line {endpoint.line})")
        print(f"    allow_guest  {endpoint.allow_guest}")
        print(f"    roles        {role_text(endpoint)}")
        print(f"    owner        {endpoint.owner}")
        print(f"    exposure     {exposure_text(endpoint)}")
        print(f"    gate         {'FAIL — ' + endpoint.blocker if endpoint.blocker else 'PASS'}")

    blockers = [endpoint for endpoint in endpoints if endpoint.blocker]
    guests = sum(endpoint.allow_guest == YES for endpoint in endpoints)
    guarded = sum(bool(endpoint.roles) for endpoint in endpoints)
    owners = sum(endpoint.owner == YES for endpoint in endpoints)
    print(
        f"\nsummary — {len(endpoints)} whitelisted; {guests} allow guest; "
        f"{guarded} role-guarded; {owners} owner-constrained"
    )
    if blockers:
        print(f"RESULT: FAIL — {len(blockers)} ownership blocker(s):")
        for endpoint in blockers:
            print(f"  {endpoint.name}: {endpoint.blocker}")
    else:
        print("RESULT: PASS — no caller-addressable record or financial aggregate lacks ownership")
    return not blockers


def report_doctypes(doctypes):
    print(f"\ndoctype surface — {METADATA_PATH}")
    print(f"  JSON + {HOOKS_PATH.name} AST only")
    for doctype in doctypes:
        print(f"\n  {doctype.name}")
        print(f"    child table       {'YES — permissions come from the parent' if doctype.child_table else 'NO'}")
        print(f"    permission query  {doctype.permission_query}")
        if doctype.child_table:
            print("    permissions       NONE OF ITS OWN — inherited from the parent DocType")
        elif not doctype.permissions:
            print("    permissions       NONE DECLARED")
        else:
            for permission in doctype.permissions:
                flags = ", ".join(permission.flags) if permission.flags else "NONE"
                print(
                    f"    permission        role={permission.role}; "
                    f"flags={flags}; if_owner={permission.if_owner}"
                )

        if doctype.name == SALE_DOCTYPE:
            if doctype.blockers:
                for role, reason in doctype.blockers:
                    print(f"    gate              FAIL — {role}: {reason}")
            else:
                print("    gate              PASS")

    blockers = [
        (doctype.name, role, reason)
        for doctype in doctypes
        for role, reason in doctype.blockers
    ]
    if blockers:
        print(f"\nDOCTYPE RESULT: FAIL — {len(blockers)} sale-list ownership blocker(s):")
        for name, role, reason in blockers:
            print(f"  {name} / {role}: {reason}")
    else:
        print("\nDOCTYPE RESULT: PASS — the sale list declares row scope for every reader")
    return not blockers


def workspace_gate_text(workspace):
    roles = ", ".join(workspace.roles) if workspace.roles else "NONE"
    return (
        f"public={workspace.public}; roles={roles}; "
        f"role restriction={workspace.role_restriction}"
    )


def report_metadata(reports, cards, workspaces):
    print("\nreport / card / workspace surface")
    print("  JSON + report AST only")

    print("\n  reports")
    if not reports:
        print("    NONE")
    for item in reports:
        roles = ", ".join(item.roles) if item.roles else "NONE"
        print(f"\n    {item.name}")
        print(f"      ref DocType      {item.ref_doctype}")
        print(f"      roles            {roles}")
        print(f"      row permissions  {item.row_permissions}")
        if item.blockers:
            for role, reason in item.blockers:
                print(f"      gate             FAIL — {role}: {reason}")
        elif item.ref_doctype == SALE_DOCTYPE:
            print("      gate             PASS")

    print("\n  number cards")
    if not cards:
        print("    NONE")
    for card in cards:
        print(f"\n    {card.name}")
        print(f"      document type    {card.document_type}")
        print(f"      method           {card.method}")
        print(f"      public           {card.public}")
        if not card.workspaces:
            print("      workspace gate   NONE FOUND")
        for workspace in card.workspaces:
            print(f"      workspace gate   {workspace.name}: {workspace_gate_text(workspace)}")
        visibility = "OPEN" if card.blocker else "GATED"
        print(f"      visibility       {visibility}")

    print("\n  workspaces")
    if not workspaces:
        print("    NONE")
    for workspace in workspaces:
        cards_text = ", ".join(workspace.number_cards) if workspace.number_cards else "NONE"
        print(f"\n    {workspace.name}")
        print(f"      {workspace_gate_text(workspace)}")
        print(f"      number cards     {cards_text}")

    blockers = [
        (item.name, role, reason)
        for item in reports
        for role, reason in item.blockers
    ]
    if blockers:
        print(f"\nREPORT RESULT: FAIL — {len(blockers)} sale-report ownership blocker(s):")
        for name, role, reason in blockers:
            print(f"  {name} / {role}: {reason}")
    else:
        print("\nREPORT RESULT: PASS — sale reports respect row permissions")
    return not blockers


def endpoint_allows_role(endpoint, role):
    if not endpoint.roles:
        return True
    if endpoint.roles_unknown:
        return True
    return role in endpoint.allowed_roles


def combined_verdict(endpoints, doctypes, reports, workspaces):
    roles = {
        permission.role
        for doctype in doctypes
        for permission in doctype.permissions
        if permission.role != "UNKNOWN"
    }
    roles.update(
        role
        for item in reports
        for role in item.roles
        if role != "UNKNOWN"
    )
    roles.update(
        role
        for workspace in workspaces
        for role in workspace.roles
        if role != "UNKNOWN"
    )

    sale_doctype = next((doctype for doctype in doctypes if doctype.name == SALE_DOCTYPE), None)
    print("\ncombined verdict — role reach to sale data")
    combined = {}
    for role in sorted(roles):
        surfaces = []
        if any(endpoint.sale_data and endpoint_allows_role(endpoint, role) for endpoint in endpoints):
            surfaces.append("whitelisted endpoint")
        if sale_doctype and any(
            permission.role == role and permission.reads_rows
            for permission in sale_doctype.permissions
        ):
            surfaces.append("Crypto Sale DocType list")
        if any(item.ref_doctype == SALE_DOCTYPE and role in item.roles for item in reports):
            names = sorted(
                item.name
                for item in reports
                if item.ref_doctype == SALE_DOCTYPE and role in item.roles
            )
            surfaces.extend(f"{name} report" for name in names)
        combined[role] = surfaces
        detail = "; ".join(surfaces) if surfaces else "NONE"
        print(f"  {role}: {len(surfaces)} way(s) — {detail}")

    sales_user = combined.get("Sales User", [])
    print(
        f"COMBINED VERDICT: Sales User reaches sale data {len(sales_user)} ways — "
        + "; ".join(sales_user)
    )
    return combined


def main():
    endpoint_controls_pass = negative_control()
    metadata_controls_pass = metadata_controls()
    controls_pass = endpoint_controls_pass and metadata_controls_pass
    try:
        source = API_PATH.read_text(encoding="utf-8")
        endpoints = analyze_source(source, str(API_PATH))
        documents = json_documents(METADATA_PATH)
        hooks_source = HOOKS_PATH.read_text(encoding="utf-8")
        doctypes, reports, cards, workspaces = scan_metadata(documents, hooks_source)
    except (OSError, SyntaxError, json.JSONDecodeError, ValueError) as error:
        print(f"\nRESULT: FAIL — cannot audit source metadata: {error}")
        return 1

    endpoint_pass = report(endpoints, API_PATH)
    doctype_pass = report_doctypes(doctypes)
    metadata_pass = report_metadata(reports, cards, workspaces)
    combined_verdict(endpoints, doctypes, reports, workspaces)
    if not controls_pass:
        print("\nRESULT: FAIL — a control failed, so the surface verdict is not trustworthy")
    elif endpoint_pass and doctype_pass and metadata_pass:
        print("\nRESULT: PASS — all three sale-data surfaces declare caller row scope")
    else:
        failed = []
        if not endpoint_pass:
            failed.append("endpoint")
        if not doctype_pass:
            failed.append("DocType list")
        if not metadata_pass:
            failed.append("report")
        print(f"\nRESULT: FAIL — unscoped sale data remains on: {', '.join(failed)}")
    return 0 if controls_pass and endpoint_pass and doctype_pass and metadata_pass else 1


if __name__ == "__main__":
    sys.exit(main())
