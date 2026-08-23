app_name = "cryptopos"
app_title = "CryptoPoS"
app_publisher = "the maintainer"
app_description = "Watch-only crypto point-of-sale terminal"
app_email = "dowoop@users.noreply.github.com"
app_license = "mit"

# Apps
# ------------------

# required_apps = []

# Each item in the list will be shown as an app in the apps page
# add_to_apps_screen = [
# 	{
# 		"name": "cryptopos",
# 		"logo": "/assets/cryptopos/logo.png",
# 		"title": "CryptoPoS",
# 		"route": "/cryptopos",
# 		"has_permission": "cryptopos.api.permission.has_app_permission"
# 	}
# ]

# Includes in <head>
# ------------------

# include js, css files in header of desk.html
# app_include_css = "/assets/cryptopos/css/cryptopos.css"
# app_include_js = "/assets/cryptopos/js/cryptopos.js"

# include js, css files in header of web template
# web_include_css = "/assets/cryptopos/css/cryptopos.css"
# web_include_js = "/assets/cryptopos/js/cryptopos.js"

# include custom scss in every website theme (without file extension ".scss")
# website_theme_scss = "cryptopos/public/scss/website"

# include js, css files in header of web form
# webform_include_js = {"doctype": "public/js/doctype.js"}
# webform_include_css = {"doctype": "public/css/doctype.css"}

# include js in page
# page_js = {"page" : "public/js/file.js"}

# include js in doctype views
# doctype_js = {"doctype" : "public/js/doctype.js"}
# doctype_list_js = {"doctype" : "public/js/doctype_list.js"}
# doctype_tree_js = {"doctype" : "public/js/doctype_tree.js"}
# doctype_calendar_js = {"doctype" : "public/js/doctype_calendar.js"}

# Svg Icons
# ------------------
# include app icons in desk
# app_include_icons = "cryptopos/public/icons.svg"

# Home Pages
# ----------

# application home page (will override Website Settings)
# home_page = "login"

# website user home page (by Role)
# role_home_page = {
# 	"Role": "home_page"
# }

# Generators
# ----------

# automatically create page for each record of this doctype
# website_generators = ["Web Page"]

# automatically load and sync documents of this doctype from downstream apps
# importable_doctypes = [doctype_1]

# Jinja
# ----------

# add methods and filters to jinja environment
# jinja = {
# 	"methods": "cryptopos.utils.jinja_methods",
# 	"filters": "cryptopos.utils.jinja_filters"
# }

# Installation
# ------------

# before_install = "cryptopos.install.before_install"
# after_install = "cryptopos.install.after_install"

# Uninstallation
# ------------

# before_uninstall = "cryptopos.uninstall.before_uninstall"
# after_uninstall = "cryptopos.uninstall.after_uninstall"

# Integration Setup
# ------------------
# To set up dependencies/integrations with other apps
# Name of the app being installed is passed as an argument

# before_app_install = "cryptopos.utils.before_app_install"
# after_app_install = "cryptopos.utils.after_app_install"

# Integration Cleanup
# -------------------
# To clean up dependencies/integrations with other apps
# Name of the app being uninstalled is passed as an argument

# before_app_uninstall = "cryptopos.utils.before_app_uninstall"
# after_app_uninstall = "cryptopos.utils.after_app_uninstall"

# Build
# ------------------
# To hook into the build process

# after_build = "cryptopos.build.after_build"

# Desk Notifications
# ------------------
# See frappe.core.notifications.get_notification_config

# notification_config = "cryptopos.notifications.get_notification_config"

# Permissions
# -----------
# Permissions evaluated in scripted ways

# permission_query_conditions = {
# 	"Event": "frappe.desk.doctype.event.event.get_permission_query_conditions",
# }
#
# has_permission = {
# 	"Event": "frappe.desk.doctype.event.event.has_permission",
# }

# Document Events
# ---------------
# Hook on document methods and events

# doc_events = {
# 	"*": {
# 		"on_update": "method",
# 		"on_cancel": "method",
# 		"on_trash": "method"
# 	}
# }

# Scheduled Tasks
# ---------------

# scheduler_events = {
# 	"all": [
# 		"cryptopos.tasks.all"
# 	],
# 	"daily": [
# 		"cryptopos.tasks.daily"
# 	],
# 	"hourly": [
# 		"cryptopos.tasks.hourly"
# 	],
# 	"weekly": [
# 		"cryptopos.tasks.weekly"
# 	],
# 	"monthly": [
# 		"cryptopos.tasks.monthly"
# 	],
# }

# Testing
# -------

# before_tests = "cryptopos.install.before_tests"

# Extend DocType Class
# ------------------------------
#
# Specify custom mixins to extend the standard doctype controller.
# extend_doctype_class = {
# 	"Task": "cryptopos.custom.task.CustomTaskMixin"
# }

# Overriding Methods
# ------------------------------
#
# override_whitelisted_methods = {
# 	"frappe.desk.doctype.event.event.get_events": "cryptopos.event.get_events"
# }
#
# each overriding function accepts a `data` argument;
# generated from the base implementation of the doctype dashboard,
# along with any modifications made in other Frappe apps
# override_doctype_dashboards = {
# 	"Task": "cryptopos.task.get_dashboard_data"
# }

# exempt linked doctypes from being automatically cancelled
#
# auto_cancel_exempted_doctypes = ["Auto Repeat"]

# Ignore links to specified DocTypes when deleting documents
# -----------------------------------------------------------

# ignore_links_on_delete = ["Communication", "ToDo"]

# Request Events
# ----------------
# before_request = ["cryptopos.utils.before_request"]
# after_request = ["cryptopos.utils.after_request"]

# Job Events
# ----------
# before_job = ["cryptopos.utils.before_job"]
# after_job = ["cryptopos.utils.after_job"]

# User Data Protection
# --------------------

# user_data_fields = [
# 	{
# 		"doctype": "{doctype_1}",
# 		"filter_by": "{filter_by}",
# 		"redact_fields": ["{field_1}", "{field_2}"],
# 		"partial": 1,
# 	},
# 	{
# 		"doctype": "{doctype_2}",
# 		"filter_by": "{filter_by}",
# 		"partial": 1,
# 	},
# 	{
# 		"doctype": "{doctype_3}",
# 		"strict": False,
# 	},
# 	{
# 		"doctype": "{doctype_4}"
# 	}
# ]

# Authentication and authorization
# --------------------------------

# auth_hooks = [
# 	"cryptopos.auth.validate"
# ]

# Automatically update python controller files with type annotations for this app.
# export_python_type_annotations = True

# default_log_clearing_doctypes = {
# 	"Logging DocType Name": 30  # days to retain logs
# }

# Translation
# ------------
# List of apps whose translatable strings should be excluded from this app's translations.
# ignore_translatable_strings_from = []



# ---------------------------------------------------------------------------
# CryptoPoS wiring
# ---------------------------------------------------------------------------

after_install = "cryptopos.install.after_install"

# The desk sidebar needs no hook: `workspace_sidebar/cryptopos.json` is picked
# up by the framework's own sync on install and on every migrate. See the note
# in install.py for what used to be here and why it went.

# Puts CryptoPoS on the /apps switcher beside ERPNext and Frappe HR. Without
# it the app has no entry point of its own.
#
# The route is /desk, not /app. Both reach the workspace -- /app/* 301s to
# /desk/* -- but ERPNext and Frappe HR are registered on /desk, and an app that
# only arrives via a compatibility redirect is one deprecation away from having
# no entry point again.
add_to_apps_screen = [
	{
		"name": app_name,
		"logo": "/assets/cryptopos/images/cryptopos-logo.svg",
		"title": app_title,
		"route": "/desk/cryptopos",
	}
]

# The heartbeat. A real terminal polls on a timer rather than waiting to be
# asked, so a sale that settles while nobody is looking at the screen still
# settles. Single-stepping from the UI calls the same function, so the timer
# and the button cannot drift apart.
#
# The minute here is only real if the bench agrees: Frappe evaluates cron on
# its scheduler tick, and that tick defaults to FOUR minutes
# (DEFAULT_SCHEDULER_TICK in frappe/utils/scheduler.py), which would silently
# turn this into a four-minute poll and leave a paid sale unacknowledged on
# the counter for that long. The bench must carry
# `"scheduler_tick_interval": 60` in common_site_config.json.
scheduler_events = {
	"cron": {
		"* * * * *": [
			"cryptopos.watch.heartbeat",
		],
		# Booking runs someone else's validation, so it can fail for reasons
		# that have nothing to do with the sale -- and `confirmed` is a state
		# the heartbeat does not poll, so nothing retried it. Every five
		# minutes rather than every minute: a failed booking is waiting on a
		# human fixing configuration, not on a block.
		"*/5 * * * *": [
			"cryptopos.settle.sweep_unbooked",
		],
	}
}
