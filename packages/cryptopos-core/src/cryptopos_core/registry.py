"""Registration and discovery for independently installed payment rails."""

import importlib.metadata
import inspect

from .errors import DuplicateRail, InvalidRailPlugin, RailNotInstalled
from .plugin import KNOWN_CAPABILITIES, Asset, Network, PaymentRail, binding_category_for

ENTRY_POINT_GROUP = "cryptopos.rails"


def validate_plugin(plugin):
	"""Return a structurally valid plugin or raise a documented plugin error."""
	if not isinstance(plugin, PaymentRail):
		raise InvalidRailPlugin("the object does not implement the PaymentRail protocol")
	if not isinstance(plugin.network, Network) or not isinstance(plugin.asset, Asset):
		raise InvalidRailPlugin("plugin network and asset must use the core identity values")
	if not isinstance(plugin.key, str) or plugin.key != f"{plugin.network.key}/{plugin.asset.key}":
		raise InvalidRailPlugin("plugin key must be '<network key>/<asset key>'")
	if not isinstance(plugin.capabilities, frozenset):
		raise InvalidRailPlugin("capabilities must be a frozenset")
	binding_category_for(plugin)
	call_shapes = {
		"readiness": (1,),
		"capture_baseline": (2,),
		"validate_recipient": (1,),
		"create_request": (1,),
		"observe": (2, 3),
		"settle": (2, 3),
	}
	for method, argument_counts in call_shapes.items():
		operation = getattr(plugin, method, None)
		if not callable(operation):
			raise InvalidRailPlugin(f"plugin {method} must be callable")
		try:
			signature = inspect.signature(operation)
			for argument_count in argument_counts:
				signature.bind(*([None] * argument_count))
		except (TypeError, ValueError):
			raise InvalidRailPlugin(f"plugin {method} does not accept the PaymentRail call shape") from None
	unknown = plugin.capabilities - KNOWN_CAPABILITIES
	if unknown:
		raise InvalidRailPlugin(f"unknown capabilities: {', '.join(sorted(unknown))}")
	return plugin


class RailRegistry:
	"""An explicit registry; discovery is opt-in and has no import-time side effects."""

	def __init__(self):
		self._rails = {}

	def register(self, plugin):
		plugin = validate_plugin(plugin)
		if plugin.key in self._rails:
			raise DuplicateRail(plugin.key)
		self._rails[plugin.key] = plugin
		return plugin

	def get(self, rail_key):
		try:
			return self._rails[rail_key]
		except (KeyError, TypeError):
			raise RailNotInstalled(rail_key) from None

	def keys(self):
		return tuple(sorted(self._rails))

	def discover(self, group=ENTRY_POINT_GROUP):
		"""Load and register every plugin in an entry-point group.

		An entry point may expose a plugin object or a zero-argument factory. A
		factory is called only when the loaded object is not already a rail.
		"""
		points = importlib.metadata.entry_points()
		selected = points.select(group=group) if hasattr(points, "select") else points.get(group, ())
		loaded = []
		for point in selected:
			candidate = point.load()
			if not isinstance(candidate, PaymentRail) and callable(candidate):
				candidate = candidate()
			loaded.append(self.register(candidate))
		return tuple(loaded)

	def register_builtins(self):
		"""Register the package's truthful built-in test-network catalog."""
		from .catalog import builtin_rails

		return tuple(self.register(plugin) for plugin in builtin_rails())


default_registry = RailRegistry()
