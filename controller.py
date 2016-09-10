import sublime
import FuzzyFilePath.completion as Completion
import FuzzyFilePath.query as Query
import FuzzyFilePath.project.CurrentView as CurrentView
import FuzzyFilePath.project.ProjectManager as ProjectManager
import FuzzyFilePath.common.settings as Settings
from FuzzyFilePath.common.verbose import verbose
from FuzzyFilePath.common.verbose import log
from FuzzyFilePath.common.config import config


ID = "Controller"


#init
def plugin_loaded():
    """ load settings """
    update_settings()
    global_settings = sublime.load_settings(config["FFP_SETTINGS_FILE"])
    global_settings.add_on_change("update", update_settings)


def update_settings():
    """ restart projectFiles with new plugin and project settings """
    # invalidate cache
    global scope_cache
    scope_cache = {}
    # update settings
    global_settings = Settings.update()
    # update project settings
    ProjectManager.set_main_settings(global_settings)


#query
def get_filepath_completions(view):
    if not CurrentView.is_valid():
        Query.reset()
        return False

    verbose(ID, "get filepath completions")
    completions = Completion.get_filepaths(view, Query, CurrentView)

    if completions and len(completions[0]) > 0:
        Completion.start(Query.get_replacements())
        view.run_command('_enter_insert_mode') # vintageous
        log("{0} completions found".format(len(completions)))
    else:
        if Query.get_needle() is not None:
            sublime.status_message("FFP no filepaths found for '" + Query.get_needle() + "'")
        Completion.stop()

    Query.reset()
    return completions


def on_query_completion_inserted(view, post_remove):
    if Completion.is_active():
        verbose(ID, "query completion inserted")
        Completion.update_inserted_filepath(view, post_remove)
        Completion.stop()


def on_query_completion_aborted():
    Completion.stop()


#project
def on_project_focus(window):
    """ window has gained focus, rebuild file cache (in case files were removed/added) """
    verbose(ID, "refocus project")
    ProjectManager.rebuild_filecache()


def on_project_activated(window):
    """ a new project has received focus """
    verbose(ID, "activate project")
    ProjectManager.activate_project(window)


#file
def on_file_created():
    """ a new file has been created """
    ProjectManager.rebuild_filecache()


def on_file_focus(view):
    """
        1. load project of window
        2. load projectfolder of view
    """
    # let the project manager select the correct project folder
    ProjectManager.update_current_project_folder(view)
    # update current views settings
    current_project = ProjectManager.get_current_project()
    if current_project:
        CurrentView.load_current_view(view, current_project.get_directory())
    else:
        CurrentView.invalidate()