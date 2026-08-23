"""Small host-side checks for installed payment-rail plugins."""

from .errors import InvalidRailPlugin
from .plugin import Readiness
from .registry import validate_plugin


def conformance_issues(plugin, configuration):
	"""Return contract violations without turning provider unavailability into one."""
	try:
		validate_plugin(plugin)
	except InvalidRailPlugin as exception:
		return (exception.reason,)
	try:
		readiness = plugin.readiness(configuration)
	except Exception as exception:  # plugin exceptions are part of what this boundary audits
		return (f"readiness raised {type(exception).__name__}: {exception}",)
	if not isinstance(readiness, Readiness):
		return ("readiness did not return a Readiness value",)
	issues = []
	if readiness.rail_key != plugin.key:
		issues.append("readiness belongs to another rail")
	if not readiness.ready <= plugin.capabilities:
		issues.append("readiness claims a capability the plugin did not declare")
	unavailable = {capability for capability, _reason in readiness.unavailable}
	if readiness.ready & unavailable:
		issues.append("a capability is both ready and unavailable")
	if plugin.capabilities - readiness.ready - unavailable:
		issues.append("a declared capability has neither readiness nor an unavailable reason")
	return tuple(issues)


def require_conformant(plugin, configuration):
	"""Return ``plugin`` or raise one stable error describing every violation."""
	issues = conformance_issues(plugin, configuration)
	if issues:
		raise InvalidRailPlugin("; ".join(issues))
	return plugin
