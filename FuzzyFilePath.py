""" FuzzyFilePath - autocomplete filepaths

    # tasks

        - improve testing
        - add to command palette: settings, base_directory
        - query file extensions

    # bugs

        - fix mixed caching and treatment of multiple projects. Currently one instance is used for all projects
            - cache session, projectcaches by window ids(?)
            - rebuild only current folders
        - switching projects: file is not recognized as valid project file
        - trailing file extensions are not sanitized

    @version 0.1.0
    @author Sascha Goldhofer <post@saschagoldhofer.de>
"""
import sublime
import sublime_plugin
import re
import os

from FuzzyFilePath.expression import Context
from FuzzyFilePath.project.project_files import ProjectFiles
from FuzzyFilePath.project.validate import Validate
from FuzzyFilePath.common.verbose import verbose
from FuzzyFilePath.common.verbose import log
from FuzzyFilePath.common.config import config
from FuzzyFilePath.common.selection import Selection
from FuzzyFilePath.common.path import Path

project_files = None
scope_cache = {}



""" ================================================================================================================ """
""" '''''''''''''''''''''''''''''''''''''''''''''''''''''''''''''''''''''''''''''''''''''''''''''''''''''''''''''''' """

def plugin_loaded():
    """ load settings """
    settings = sublime.load_settings(config["FFP_SETTINGS_FILE"])
    settings.add_on_change("scopes", update_settings)
    update_settings()

def update_settings():
    """ restart projectFiles with new plugin and project settings """
    global project_files, scope_cache

    scope_cache.clear()
    settings = sublime.load_settings(config["FFP_SETTINGS_FILE"])
    project_settings = sublime.active_window().active_view().settings().get('FuzzyFilePath', False)

    # sync settings to config
    for key in config:
        config[key] = settings.get(key.lower(), config[key])
    # mapping
    config["TRIGGER"] = settings.get("scopes", config["TRIGGER"])
    # merge project settings stored in "settings: { FuzzyFilePath: ..."
    if project_settings:
        # mapping
        config["TRIGGER"] = project_settings.get("scopes", config["TRIGGER"])
        for key in config:
            config[key] = project_settings.get(key.lower(), config[key])
    # build extensions to suggest
    extensionsToSuggest = []
    for scope in config["TRIGGER"]:
        ext = scope.get("extensions", [])
        extensionsToSuggest += ext
    # remove duplicates
    extensionsToSuggest = list(set(extensionsToSuggest))

    project_files = ProjectFiles()
    project_files.update_settings(extensionsToSuggest, config["EXCLUDE_FOLDERS"])
    # validate directories
    if config["BASE_DIRECTORY"]:
        config["BASE_DIRECTORY"] = Path.sanitize_base_directory(config["BASE_DIRECTORY"])
    if config["PROJECT_DIRECTORY"]:
        config["PROJECT_DIRECTORY"] = Path.sanitize_base_directory(config["PROJECT_DIRECTORY"])

    log("logging enabled")
    log("project base directory set to '{0}'".format(config["BASE_DIRECTORY"]))
    log("{0} scope triggers loaded".format(len(config["TRIGGER"])))


class Completion:
    """
        Manage active state of completion and post cleanup
    """
    active = False  # completion currently in progress (servce suggestions)
    onInsert = []   # substitutions for building final path
    base_directory = False  # base directory to set for absolute path, enabled by query...

    def start(post_replacements=[]):
        Completion.replaceOnInsert = post_replacements
        Completion.active = True

    def stop():
        Completion.active = False
        # set by query....
        Completion.base_directory = False

    def is_active():
        return Completion.active

    def get_final_path(path, post_remove):
        # string to replace on post_insert_completion
        post_remove = re.escape(post_remove)
        path = re.sub("^" + post_remove, "", path)
        # hack reverse
        path = re.sub(config["ESCAPE_DOLLAR"], "$", path)
        for replace in Completion.replaceOnInsert:
            path = re.sub(replace[0], replace[1], path)

        if Completion.base_directory and path.startswith("/"):
            path = re.sub("^\/" + Completion.base_directory, "", path)
            path = Path.sanitize(path)

        return path


class FfpUpdateCacheCommand(sublime_plugin.TextCommand):
    """ force update project-files cache """
    def run(self, edit):
        project_files.rebuild()


class FfpShowInfoCommand(sublime_plugin.TextCommand):
    """ shows a message dialog with project validation status of current file """
    def run(self, edit):
       Validate.view(self.view, config, True)


class InsertPathCommand(sublime_plugin.TextCommand):
    """ trigger customized autocomplete overriding auto settings """
    def run(self, edit, type="default", base_directory=None, replace_on_insert=[], extensions=[]):
        if config["DISABLE_KEYMAP_ACTIONS"] is True:
            return False

        Query.force("filepath_type", type)
        Query.force("base_directory", base_directory)

        if len(replace_on_insert) > 0:
            Query.force("replace_on_insert", replace_on_insert)
        if len(extensions) > 0:
            Query.force("extensions", extensions)

        self.view.run_command('auto_complete', "insert")


class WindowManager(sublime_plugin.EventListener):
    """ rebuilds cache on activated window """

    previous_view = None
    previous_window = None

    # called when a view has been activated
    def on_activated(self, view):
        # simulate on window activated
        if WindowManager.previous_view is not view.id():
            WindowManager.previous_view = view.id()
        else:
            self.on_window_activated(view)

        if WindowManager.previous_window is not sublime.active_window().id():
            WindowManager.previous_window = sublime.active_window().id()
            self.on_window_changed(sublime.active_window())


    # called when a window gains focus
    def on_window_activated(self, view):
        # the window has gained focus again. possibly just a distraction, but maybe the project structure has changed.
        # Thus reload cache
        project_files.rebuild()

    # called when a different window gains focus
    def on_window_changed(self, window):
        update_settings()

        return
        # project_data seems to be window contained storage
        # has set_project_data, which persists if a project_file_name is available
        # ! this is the project settings file. Should be used to persist settings only...
        # May be used to differentiate projects over windows, but the same project is opened once most of the time
        data = window.project_data()
        if data.get("FFP"):
            # print("window recognized by data", data.get("FFP").get("id"))
            data.get("FFP").get("id")
        else:
            # id should be something like project directory...
            data["FFP"] = { "id": window.id() }
            window.set_project_data(data)
        # What about sublime-workspace?
        # (project only...)
        # if None, its an opened file or folder
        # print(window.project_file_name())


class CurrentFile(sublime_plugin.EventListener):
    """ Evaluates and caches current file`s project status """

    cache = {}
    current = {
        "is_temp": False,               # file does not exist in filesystem
        "directory": False,             # directory relative to project
        "project_directory": False      # project directory
    }

    def on_activated(self, view):
        # view has gained focus
        file_name = view.file_name()
        current = self.cache.get(file_name)

        if current is None or current.get("is_temp"):
            # add current view to cache
            current = CurrentFile.validate(view)
            CurrentFile.cache[file_name] = current
            # and update project files
            if project_files and current["project_directory"]:
                project_files.add(current["project_directory"])

        CurrentFile.current = current

    def is_valid():
        return CurrentFile.current.get("project_directory") is not False

    def get_project_directory():
        return CurrentFile.current.get("project_directory")

    def get_directory():
        return CurrentFile.current.get("directory")

    def is_temp():
        return CurrentFile.current.get("is_temp")

    def validate(view):
        current = {
            "is_temp": False,
            "directory": False,
            "project_directory": False
        }

        current["is_temp"] = not Validate.file_has_location(view)
        if current["is_temp"]:
            return current

        directory = Validate.view(view, config, False)
        if directory:
            current["project_directory"] = directory["project"]
            current["directory"] = directory["current"]

        return current


class Query:
    """
        Build current query based on received modifiers
    """
    forces = {
        # documentation only, will be removed
        "filepath_type": False,
        "extensions": [],
        "base_directory": "",
        "replace_on_insert": []
    }

    extensions = ["*"]
    base_path = False
    replace_on_insert = []

    def reset():
        Query.extensions = ["*"]
        Query.base_path = False
        Query.replace_on_insert = []
        Query.forces.clear()

    def force(key, value):
        Query.forces[key] = value

    def get(key, default=None):
        return Query.forces.get(key, default)

    def by_command():
        return bool(Query.get("filepath_type", False))

    def build(needle, trigger, current_folder, project_folder):

        query = {}

        force_type = Query.get("filepath_type", False)
        triggered = Query.by_command()
        filepath_type = "relative"
        needle = Path.sanitize(needle)
        needle_is_absolute = Path.is_absolute(needle)
        needle_is_relative = Path.is_relative(needle)
        needle_is_path = needle_is_absolute or needle_is_relative
        # abort if autocomplete is not available
        if not triggered and trigger.get("auto", False) is False and needle_is_path is False:
            # print("FFP no autocomplete")
            return False
        # test path to trigger auto-completion by needle
        if not triggered and trigger["auto"] is False and config["AUTO_TRIGGER"] and needle_is_absolute:
            force_type = "absolute"
        # base_directory: override - trigger - False
        base_directory = trigger.get("base_directory", False)
        base_directory = Query.get("base_directory", base_directory)
        #
        # set current directory by force, else by trigger:
        #
        # trigger       |
        # --------------|--------------------
        # False         | use current file's directory
        # True          | use settings: base_directory
        # String        | use string as base_directory
        # change base folder to base directory
        #
        if base_directory is True:
            current_folder = config["BASE_DIRECTORY"]
        elif base_directory:
            current_folder = Path.sanitize_base_directory(base_directory)
        # notify completion to replace path
        if base_directory and needle_is_absolute:
            Completion.base_directory = current_folder
        #
        # filepath_type
        #
        # needle    | trigger rel   | force     | RESULT
        # ----------|---------------|-----------|---------
        # ?         | relative      | False     | relative
        # ?         | absolute      | False     | absolute
        # absolute  | *             | False     | absolute
        # relative  | *             | False     | relative
        # *         | *             | relative  | relative
        # *         | *             | absolute  | absolute
        #
        if force_type:
            filepath_type = force_type
        elif needle_is_absolute:
            filepath_type = "absolute"
        elif needle_is_relative:
            filepath_type = "relative"
        elif trigger.get("relative") is True:
            filepath_type = "relative"
        elif trigger.get("relative") is False:
            filepath_type = "absolute"

        Query.base_path = current_folder if filepath_type == "relative" else False

        # replacements: override - trigger - None
        Query.replace_on_insert = trigger.get("replace_on_insert", [])
        Query.replace_on_insert = Query.get("replace_on_insert", Query.replace_on_insert)
        # extensions: override - trigger - "js"
        extensions = trigger.get("extensions", ["*"])
        extensions = Query.get("extensions", extensions)
        Query.extensions = extensions
        Query.needle = Query.build_needle_query(needle, current_folder)
        # strip any starting dots or slashes
        Query.needle = re.sub("^[\.\/]*", "", Query.needle)
        # --------------------------------------------------------------------
        # tests throw error if results are set to class
        # Require refactoring of static classes with dynamic properties?
        # --------------------------------------------------------------------
        query["extensions"] = extensions
        query["base_path"] = current_folder if filepath_type == "relative" else False
        query["needle"] = Query.build_needle_query(needle, current_folder)

        if triggered or (config["AUTO_TRIGGER"] if needle_is_path else trigger.get("auto", config["AUTO_TRIGGER"])):
            return query

        return False

    def build_needle_query(needle, current_folder):
        current_folder = "" if not current_folder else current_folder
        needle = re.sub("\.\./", "", needle)
        needle = re.sub("[\\n\\t]", "", needle)
        needle = needle.strip()
        if needle.startswith("./"):
            needle = current_folder + re.sub("\.\/", "", needle)
        return needle


def cleanup_completion(view, post_remove):
    expression = Context.get_context(view)
    # remove path query completely
    final_path = Completion.get_final_path(expression["needle"], post_remove)
    log("post cleanup path:'", expression.get("needle"), "' ~~> '", final_path, "'")
    # replace current query with final path
    view.run_command("ffp_replace_region", { "a": expression["region"].a, "b": expression["region"].b, "string": final_path })


def get_matching_autotriggers(scope, triggers):
    global scope_cache
    # get cached evaluation
    result = scope_cache.get(scope)
    if result is None:
        # evaluate triggers on current scope
        result = [trigger for trigger in triggers if trigger.get("auto") and re.search(trigger.get("scope"), scope)]
        # add to cache
        scope_cache[scope] = result if len(result) > 0 else False
        result = scope_cache.get(scope)

    return result


def query_completions(view, project_folder, current_folder):
    global Context, Selection

    current_scope = Selection.get_scope(view)

    if not Query.by_command():
        triggers = get_matching_autotriggers(current_scope, config["TRIGGER"])
    else:
        triggers = config["TRIGGER"]

    if not bool(triggers):
        log("abort query, no valid scope-regex for current context")
        return False

    # parse current context, may contain 'is_valid: False'
    expression = Context.get_context(view)
    if expression["error"] and not Query.by_command():
        verbose("abort not a valid context")
        return False

    # check if there is a trigger for the current expression
    trigger = Context.find_trigger(expression, current_scope, triggers)
    # verbose("trigger", trigger)

    # expression | trigger  | force | ACTION            | CURRENT
    # -----------|----------|-------|-------------------|--------
    # invalid    | False    | False | abort             | abort
    # invalid    | False    | True  | query needle      | abort
    # invalid    | True     | False | query             |
    # invalid    | True     | True  | query +override   |
    # valid      | False    | False | abort             | abort
    # valid      | False    | True  | query needle      | abort
    # valid      | True     | False | query             |
    # valid      | True     | True  | query +override   |

    # currently trigger is required in Query.build
    if trigger is False:
        log("abort completion, no trigger found")
        return False

    if not expression["valid_needle"]:
        word = Selection.get_word(view)
        expression["needle"] = re.sub("[^\.A-Za-z0-9\-\_$]", "", word)
        log("changed invalid needle to {0}".format(expression["needle"]))
    else:
        log("context evaluation {0}".format(expression))

    if Query.build(expression.get("needle"), trigger, current_folder, project_folder) is False:
        # query is valid, but may not be triggered: not forced, no auto-options
        log("abort valid query: auto trigger disabled")
        return False

    if (config["LOG"]):
        log("query completions")
        log("────────────────────────────────────────────────────────────────")
        log("scope settings: {0}".format(trigger))
        log("search needle: '{0}'".format(Query.needle))
        log("in base path: '{0}'".format(Query.base_path))

    completions = project_files.search_completions(Query.needle, project_folder, Query.extensions, Query.base_path)

    if completions and len(completions[0]) > 0:
        Completion.start(Query.replace_on_insert)
        view.run_command('_enter_insert_mode') # vintageous
        log("=> {0} completions found".format(len(completions)))
    else:
        sublime.status_message("FFP no filepaths found for '" + Query.needle + "'")
        Completion.stop()
        log("=> no valid files found for needle: {0}".format(Query.needle))

    log("")

    Query.reset()
    return completions


class FuzzyFilePath(sublime_plugin.EventListener):

    # tracks on_post_insert_completion
    track_insert = {
        "active": False,
        "start_line": "",
        "end_line": ""
    }
    post_remove = ""

    def on_query_completions(self, view, prefix, locations):
        if config["DISABLE_AUTOCOMPLETION"] and not Query.by_command():
            return False

        if self.track_insert["active"] is False:
            self.start_tracking(view)

        print(CurrentFile.is_valid(), CurrentFile.get_project_directory(), CurrentFile.get_directory())

        if CurrentFile.is_valid():
            return query_completions(view, CurrentFile.get_project_directory(), CurrentFile.get_directory())

        return False

    def on_post_insert_completion(self, view, command_name):
        if Completion.is_active():
            cleanup_completion(view, self.post_remove)
            Completion.stop()

    # update project by file
    def on_post_save_async(self, view):
        if CurrentFile.is_temp():
            # but saved now:
            verbose("temp file saved, reevaluate")
            self.on_activated(view)

        if project_files is None:
            return False

        folders = sublime.active_window().folders()
        match = [folder for folder in folders if folder in view.file_name()]
        if len(match) > 0:
            return project_files.update(match[0], view.file_name())
        else:
            return False

    # track post insert insertion
    def start_tracking(self, view, command_name=None):
        self.track_insert["active"] = True
        self.track_insert["start_line"] = Selection.get_line(view)
        self.track_insert["end_line"] = None
        """
            - sublime inserts completions by replacing the current word
            - this results in wrong path insertions if the query contains word_separators like slashes
            - thus the path until current word has to be removed after insertion
            - ... and possibly afterwards
        """
        context = Context.get_context(view)
        needle = context.get("needle")
        word = re.escape(Selection.get_word(view))
        self.post_remove = re.sub(word + "$", "", needle)

    def finish_tracking(self, view, command_name=None):
        self.track_insert["active"] = False
        self.track_insert["end_line"] = Selection.get_line(view)

    def abort_tracking(self):
        self.track_insert["active"] = False

    def on_text_command(self, view, command_name, args):
        # check if a completion may be inserted
        if command_name in config["TRIGGER_ACTION"] or command_name in config["INSERT_ACTION"]:
            self.start_tracking(view, command_name)
        elif command_name == "hide_auto_complete":
            Completion.stop()
            self.abort_tracking()

    # check if a completion is inserted and trigger on_post_insert_completion
    def on_post_text_command(self, view, command_name, args):
        current_line = Selection.get_line(view)
        command_trigger = command_name in config["TRIGGER_ACTION"] and self.track_insert["start_line"] != current_line
        if command_trigger or command_name in config["INSERT_ACTION"]:
            self.finish_tracking(view, command_name)
            self.on_post_insert_completion(view, command_name)
