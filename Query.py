import re, os

from FuzzyFilePath.common.verbose import verbose

def posix(path):
    return path.replace("\\", "/")

class Query:

    auto_trigger = False
    valid = False
    current_folder = None
    project_folder = None
    skip_update_replace = False

    def __init__(self):
        self.reset()

    def reset(self):
        self.extensions = ["*"]
        self.relative = False
        self.active = False
        self.extension = True
        self.replace_on_insert = []
        self.skip_update_replace = False


    def build(self, needle, properties, force_type=False):
        """ Setup properties for completion query

            Behaviour
            - replaces starting ./ with current folder
            - uses all extensions if completion is triggered and not specified in settings
            - triggers completion if
              - triggered manually
              - scope settings found and auto true OR
              - auto_trigger is set to true and input is path
            - inserts path relative if
              - set in settings and true OR if not false
              - path starts with ../ or ./
              - triggered manually (overrides all)

            Parameters:
            -----------
            current_scope -- complete scope on current cursor position
            needle -- path to search
            force_type -- "default", "relative", "absolute" (default False)
        """
        triggered = force_type is not False

        query_string = self.get_input_properties(needle)

        if triggered is False and properties is False and query_string["is_path"] is False:
            return False

        self.needle = query_string["needle"] # resolved needle for "./" or "../"
        self.relative = query_string["relative"] # default current string

        if properties:
            self.active = properties.get("auto", self.auto_trigger)
            self.extension = properties.get("insertExtension", True)
            self.extensions = properties.get("extensions", ["js"])
            self.relative = properties.get("relative", query_string["relative"])
            if not self.skip_update_replace:
                self.replace_on_insert = properties.get("replace_on_insert", [])

        # TEST: ignore property settings
        if query_string["is_path"]:
            self.active = self.auto_trigger

        if self.relative is None:
            self.relative = False

        if force_type is not False:
            self.active = True

            if force_type is not "default":
                self.relative = force_type == "relative"

        if self.relative is True:
            self.relative = self.current_folder
        elif self.relative is None:
            self.relative = False

        return self.active

    def override_replace_on_insert(self, replacements):
        self.replace_on_insert = replacements
        self.skip_update_replace = True

    def get_input_properties(self, needle):
        properties = {
            "is_path": False,
            "relative": False,
            "needle": needle
        }

        needle = re.sub("^(./)+", "./", needle)

        if needle.startswith("./"):
            properties["is_path"] = True
            properties["relative"] = self.current_folder
            properties["needle"] = needle.replace("./", self.current_folder)

        elif needle.startswith("../"):
            properties["is_path"] = True
            properties["relative"] = self.current_folder
            properties["needle"] = needle.replace("../", "")

        elif re.search("^\/[A-Za-z0-9\_\-\s\.]*\/", needle):
            properties["is_path"] = True
            properties["relative"] = False
            properties["needle"] = needle

        return properties
