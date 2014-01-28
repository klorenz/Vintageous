import sublime, os, sys, json
import sublime_plugin

import re

def console_log(s, *args):
    sys.stderr.write('[SublimeModelines] '+(s % args)+"\n")

def debug_log(s, *args):
    if 1:
        sys.stderr.write('[SublimeModelines] '+(s % args)+"\n")

MODELINE_PREFIX_TPL = "%s\\s*(st|sublime|vim):"
DEFAULT_LINE_COMMENT = '#'
MULTIOPT_SEP = '; '
MAX_LINES_TO_CHECK = 50
LINE_LENGTH = 80
MODELINES_REG_SIZE = MAX_LINES_TO_CHECK * LINE_LENGTH

MODELINE_TYPE_1  = re.compile(r"[\x20\t](st|sublime|vim):\x20?set\x20(.*):.*$")
MODELINE_TYPE_2  = re.compile(r"[\x20\t](st|sublime|vim):(.*):.*$")

MONITORED_OUTPUT_PANELS = ['exec']

KEY_VALUE = re.compile(r"""(?x) \s*
    (?P<key>\w+)  \s* (?P<op>\+?=)  \s*  (?P<value>
        (?:  "(?:\\.|[^"\\])*"
            | [\[\{].*
            | [^\s:]+
            ))
    """)

KEY_ONLY  = re.compile(r"""(?x)\s*(?P<key>\w+)""")

VIM_MAP = {
    "ts": "tabstop",
    "tabstop": "tab_size",
    "ai": "autoindent",
    "autoindent": "auto_indent",
    "et": "expandtab",
    "expandtab": "translate_tabs_to_spaces",
    "syn": "syntax",
    "nu": "number",
    "number": "line_numbers",
}


def vim_mapped(t, s):
    if t == 'vim' or len(s) < 3:
        while s in VIM_MAP:
            s = VIM_MAP[s]
    return s


def get_language_files(ignored_packages, *paths):
    r'''
    Get a list of language files respecting ignored packages.
    '''

    paths = list(paths)
    tml_files = []
    tml_files.extend(sublime.find_resources('*.tmLanguage'))

    for path in paths:
        for dir, dirs, files in os.walk(path):
            # TODO: be sure that not tmLanguage from disabled package is taken
            for fn in files:
                if fn.endswith('.tmLanguage'):
                    tml_files.append(os.path.join(dir, fn))

    R = re.compile("Packages[\\/]([^\\/]+)[\\/]")
    result = []
    for f in tml_files:
        m = R.search(f)
        if m:
            if m.group(1) not in ignored_packages:
                result.append(f)

    return result


def get_output_panel(name):
    return sublime.active_window().create_output_panel(name)


def is_modeline(prefix, line):
    return bool(re.match(prefix, line))


def gen_modelines(view):
    topRegEnd = min(MODELINES_REG_SIZE, view.size())
    candidates = view.lines(sublime.Region(0, view.full_line(topRegEnd).end()))

    # Consider modelines at the end of the buffer too.
    # There might be overlap with the top region, but it doesn't matter because
    # it means the buffer is tiny.
    pt = view.size() - MODELINES_REG_SIZE
    bottomRegStart = pt if pt > -1 else 0
    candidates += view.lines(sublime.Region(bottomRegStart, view.size()))

    prefix = build_modeline_prefix(view)
    modelines = (view.substr(c) for c in candidates if is_modeline(prefix, view.substr(c)))

    for modeline in modelines:
        yield modeline


def gen_vim_compatible_options(modeline):
    match = MODELINE_TYPE_1.search(modeline)
    if not match:
        match = MODELINE_TYPE_2.search(modeline)

    if not match: yield None

    type, s = match.groups()

    while True:
        if s.startswith(':'): s = s[1:]

        m = KEY_VALUE.match(s)
        if m:
            key, op, value = m.groups()
            yield vim_mapped(type, key), op, value
            s = s[m.end():]
            continue

        m = KEY_ONLY.match(s)
        if m:
            k, = m.groups()
            value = "true"

            _k = vim_mapped(type, k)
            if (k.startswith('no') and (type == 'vim' or (
                k[2:] in VIM_MAP or len(k) <= 4))):

                value = "false"
                _k = vim_mapped(type, k[2:])

            yield _k, '=', value

            s = s[m.end():]
            continue

        break


def gen_raw_options(modelines):
    for m in modelines:
        for opt in gen_vim_compatible_options(m):
            if opt is None: break
            yield opt

        if opt is not None:
            continue

        opt = m.partition(':')[2].strip()
        if MULTIOPT_SEP in opt:
            for subopt in (s for s in opt.split(MULTIOPT_SEP)):
                yield subopt
        else:
            yield opt


def gen_modeline_options(view):
    modelines = gen_modelines(view)
    for opt in gen_raw_options(modelines):

        if not isinstance(opt, tuple):
            name, sep, value = opt.partition(' ')
            yield view.settings().set, name.rstrip(':'), value.rstrip(';')
            continue

        name, op, value = opt
        def _setter(n,v):
            #import spdb ; spdb.start()
            if op == '+=':
                if v.startswith('{'):
                    default = {}
                elif v.startswith('['):
                    default = []
                elif isinstance(v, basestring):
                    default = ""
                else:
                    default = 0

                ov = view.settings().get(n, default)
                v = ov + v

            view.settings().set(n,v)

        yield _setter, name, value


def get_line_comment_char(view):
    commentChar = ""
    commentChar2 = ""
    try:
        for pair in view.meta_info("shellVariables", 0):
            if pair["name"] == "TM_COMMENT_START":
                commentChar = pair["value"]
            if pair["name"] == "TM_COMMENT_START_2":
                commentChar2 = pair["value"]
            if commentChar and commentChar2:
                break
    except TypeError:
        pass

    if not commentChar2:
        return re.escape(commentChar.strip())
    else:
        return "(" + re.escape(commentChar.strip()) + "|" + re.escape(commentChar2.strip()) + ")"

def build_modeline_prefix(view):
    lineComment = get_line_comment_char(view).lstrip() or DEFAULT_LINE_COMMENT
    return (MODELINE_PREFIX_TPL % lineComment)


def to_json_type(v):
    """"Convert string value to proper JSON type.
    """
    try:
        result = json.loads(v.strip())
        debug_log("%s -> %s", v, result)
        return result
    except Exception as e:
        debug_log("Exception: %s", e)
        if v:
            if v[0] not in "[{":
                return v
        raise ValueError("Could not convert from JSON: %s" % v)



class ExecuteSublimeTextModeLinesCommand(sublime_plugin.EventListener):
    """This plugin provides a feature similar to vim modelines.
    Modelines set options local to the view by declaring them in the
    source code file itself.

        Example:
        mysourcecodefile.py
        # sublime: gutter false
        # sublime: translate_tab_to_spaces true

    The top as well as the bottom of the buffer is scanned for modelines.
    MAX_LINES_TO_CHECK * LINE_LENGTH defines the size of the regions to be
    scanned.
    """

    def do_syntax(self, view, value, settings):
        syntax_file = None

        ignored_packages = settings.get('ignored_packages')
        base_dir = settings.get('result_base_dir')

        if os.path.isabs(value):
            syntax_file = value

            if not os.path.exists(syntax_file):
                console_log("%s does not exist", value)
                return

        else:
            # be smart about syntax:
            if base_dir: 
                lang_files = get_language_files(ignored_packages, base_dir)
            else:
                lang_files = get_language_files(ignored_packages)

            candidates = []
            for syntax_file in lang_files:
                if value in os.path.basename(syntax_file):
                    candidates.append(syntax_file)

            value_lower = value.lower()
            if not candidates:
                for syntax_file in lang_files:
                    if value_lower in os.path.basename(syntax_file).lower():
                        candidates.append(syntax_file)

            if not candidates:
                console_log("%s cannot be resolved to a syntaxfile", value)
                syntax_file = None
                return

            else:
                candidates.sort(key=lambda x: len(os.path.basename(x)))
                syntax_file = candidates[0]

            if not settings.get('vintageous_orig_syntax', False):
                if settings.get('syntax'):
                    settings.set('vintageous_orig_syntax', settings.get('syntax'))

            view.assign_syntax(syntax_file)

    def do_modelines(self, view):
        settings = view.settings()
        keys = set(settings.get('vintageous_modeline_keys', []))
        print("keys: %s" % keys)
        new_keys = set()

        for setter, name, value in gen_modeline_options(view):
            debug_log("%s = %s", name, value)
            if name in ('x_syntax', 'syntax'):
                self.do_syntax(view, value, settings)
                new_keys.add('syntax')
                continue

            try:
                setter(name, to_json_type(value))
                new_keys.add(name)
            except ValueError as e:
                sublime.status_message("[SublimeModelines] Bad modeline detected.")
                print ("[SublimeModelines] Bad option detected: %s, %s" % (name, value))
                print ("[SublimeModelines] Tip: Keys cannot be empty strings.")

        for k in keys:
            if k not in new_keys:
                if settings.has(k):
                    if k == 'syntax':
                        if settings.has('vintageous_orig_syntax'):
                            if settings.get('vintageous_orig_syntax'):
                                view.assign_syntax(settings.get('vintageous_orig_syntax'))
                            #settings.erase('vintageous_orig_syntax')
                    else:
                        settings.erase(k)

        settings.set('vintageous_modeline_keys', list(new_keys))


    def on_load(self, view):
        self.do_modelines(view)

    def on_post_save(self, view):
        self.do_modelines(view)

    def on_modified(self, view):
        monitored_output_panels = view.settings().get('vintageous_monitored_output_panels', MONITORED_OUTPUT_PANELS)

        for p in monitored_output_panels:
            v = get_output_panel(p)

            if v.id() != view.id():
                continue

            if v.settings().get('vintageous_output_panel_done', False):
                continue

            if v.size() > MODELINES_REG_SIZE:
                v.settings().set('vintageous_output_panel_done', True)
                self.do_modelines(v)
                continue

            self.do_modelines(v)

