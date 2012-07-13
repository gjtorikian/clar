#!/usr/bin/env python

from __future__ import with_statement
from string import Template
import re, fnmatch, os

VERSION = "0.10.0"

TEST_FUNC_REGEX = r"^(void\s+(test_%s__(\w+))\(\s*void\s*\))\s*\{"

EVENT_CB_REGEX = re.compile(
    r"^(void\s+clar_on_(\w+)\(\s*void\s*\))\s*\{",
    re.MULTILINE)

SKIP_COMMENTS_REGEX = re.compile(
    r'//.*?$|/\*.*?\*/|\'(?:\\.|[^\\\'])*\'|"(?:\\.|[^\\"])*"',
    re.DOTALL | re.MULTILINE)

CLAR_HEADER = """
/*
 * Clar v%s
 *
 * This is an autogenerated file. Do not modify.
 * To add new unit tests or suites, regenerate the whole
 * file with `./clar`
 */
""" % VERSION

CLAR_EVENTS = [
    'init',
    'shutdown',
    'test',
    'suite'
]

def main():
    from optparse import OptionParser

    parser = OptionParser()

    parser.add_option('-c', '--clar-path', dest='clar_path')
    parser.add_option('-v', '--report-to', dest='print_mode', default='default')

    options, args = parser.parse_args()

    for folder in args or ['.']:
        builder = ClarTestBuilder(folder,
            clar_path = options.clar_path,
            print_mode = options.print_mode)

        builder.render()


class ClarTestBuilder:
    def __init__(self, path, clar_path = None, print_mode = 'default'):
        self.declarations = []
        self.suite_names = []
        self.callback_data = {}
        self.suite_data = {}
        self.event_callbacks = []

        self.clar_path = os.path.abspath(clar_path) if clar_path else None

        self.path = os.path.abspath(path)
        self.modules = [
            "clar_sandbox.c",
            "clar_fixtures.c",
            "clar_fs.c",
            "clar_categorize.c",
        ]

        self.modules.append("clar_print_%s.c" % print_mode)

        print("Loading test suites...")

        for root, dirs, files in os.walk(self.path):
            module_root = root[len(self.path):]
            module_root = [c for c in module_root.split(os.sep) if c]

            tests_in_module = fnmatch.filter(files, "*.c")

            for test_file in tests_in_module:
                full_path = os.path.join(root, test_file)
                test_name = "_".join(module_root + [test_file[:-2]])

                with open(full_path) as f:
                    self._process_test_file(test_name, f.read())

        if not self.suite_data:
            raise RuntimeError(
                'No tests found under "%s"' % path)

    def render(self):
        main_file = os.path.join(self.path, 'clar_main.c')
        with open(main_file, "w") as out:
            out.write(self._render_main())

        header_file = os.path.join(self.path, 'clar.h')
        with open(header_file, "w") as out:
            out.write(self._render_header())

        print ('Written Clar suite to "%s"' % self.path)

    #####################################################
    # Internal methods
    #####################################################

    def _render_cb(self, cb):
        return '{"%s", &%s}' % (cb['short_name'], cb['symbol'])

    def _render_suite(self, suite, index):
        template = Template(
r"""
    {
        ${suite_index},
        "${clean_name}",
        ${categorize},
        ${initialize},
        ${cleanup},
        ${cb_ptr}, ${cb_count}
    }
""")

        callbacks = {}
        for cb in ['categorize', 'initialize', 'cleanup']:
            callbacks[cb] = (self._render_cb(suite[cb])
                if suite[cb] else "{NULL, NULL}")

        return template.substitute(
            suite_index = index,
            clean_name = suite['name'].replace("_", "::"),
            categorize = callbacks['categorize'],
            initialize = callbacks['initialize'],
            cleanup = callbacks['cleanup'],
            cb_ptr = "_clar_cb_%s" % suite['name'],
            cb_count = suite['cb_count']
        ).strip()

    def _render_callbacks(self, suite_name, callbacks):
        template = Template(
r"""
static const struct clar_func _clar_cb_${suite_name}[] = {
    ${callbacks}
};
""")
        callbacks = [
            self._render_cb(cb)
            for cb in callbacks
            if cb['short_name'] not in ('categorize', 'initialize', 'cleanup')
        ]

        return template.substitute(
            suite_name = suite_name,
            callbacks = ",\n\t".join(callbacks)
        ).strip()

    def _render_event_overrides(self):
        overrides = []
        for event in CLAR_EVENTS:
            if event in self.event_callbacks:
                continue

            overrides.append(
                "#define clar_on_%s() /* nop */" % event
            )

        return '\n'.join(overrides)

    def _render_header(self):
        template = Template(self._load_file('clar.h'))

        declarations = "\n".join(
            "extern %s;" % decl
            for decl in sorted(self.declarations)
        )

        return template.substitute(
            extern_declarations = declarations,
        )

    def _render_main(self):
        template = Template(self._load_file('clar.c'))
        suite_names = sorted(self.suite_names)

        suite_data = [
            self._render_suite(self.suite_data[s], i)
            for i, s in enumerate(suite_names)
        ]

        callbacks = [
            self._render_callbacks(s, self.callback_data[s])
            for s in suite_names
        ]

        callback_count = sum(
            len(cbs) for cbs in self.callback_data.values()
        )

        return template.substitute(
            clar_modules = self._get_modules(),
            clar_callbacks = "\n".join(callbacks),
            clar_suites = ",\n\t".join(suite_data),
            clar_suite_count = len(suite_data),
            clar_callback_count = callback_count,
            clar_event_overrides = self._render_event_overrides(),
        )

    def _load_file(self, filename):
        if self.clar_path:
            filename = os.path.join(self.clar_path, filename)
            with open(filename) as cfile:
                return cfile.read()

        else:
            import zlib, base64, sys
            content = CLAR_FILES[filename]

            if sys.version_info >= (3, 0):
                content = bytearray(content, 'utf_8')
                content = base64.b64decode(content)
                content = zlib.decompress(content)
                return str(content, 'utf-8')
            else:
                content = base64.b64decode(content)
                return zlib.decompress(content)

    def _get_modules(self):
        return "\n".join(self._load_file(f) for f in self.modules)

    def _skip_comments(self, text):
        def _replacer(match):
            s = match.group(0)
            return "" if s.startswith('/') else s

        return re.sub(SKIP_COMMENTS_REGEX, _replacer, text)

    def _process_test_file(self, suite_name, contents):
        contents = self._skip_comments(contents)

        self._process_events(contents)
        self._process_declarations(suite_name, contents)

    def _process_events(self, contents):
        for (decl, event) in EVENT_CB_REGEX.findall(contents):
            if event not in CLAR_EVENTS:
                continue

            self.declarations.append(decl)
            self.event_callbacks.append(event)

    def _process_declarations(self, suite_name, contents):
        callbacks = []
        categorize = initialize = cleanup = None

        regex_string = TEST_FUNC_REGEX % suite_name
        regex = re.compile(regex_string, re.MULTILINE)

        for (declaration, symbol, short_name) in regex.findall(contents):
            data = {
                "short_name" : short_name,
                "declaration" : declaration,
                "symbol" : symbol
            }

            if short_name == 'categorize':
                categorize = data
            if short_name == 'initialize':
                initialize = data
            elif short_name == 'cleanup':
                cleanup = data
            else:
                callbacks.append(data)

        if not callbacks:
            return

        tests_in_suite = len(callbacks)

        suite = {
            "name" : suite_name,
            "categorize" : categorize,
            "initialize" : initialize,
            "cleanup" : cleanup,
            "cb_count" : tests_in_suite
        }

        if categorize:
            self.declarations.append(categorize['declaration'])

        if initialize:
            self.declarations.append(initialize['declaration'])

        if cleanup:
            self.declarations.append(cleanup['declaration'])

        self.declarations += [
            callback['declaration']
            for callback in callbacks
        ]

        callbacks.sort(key=lambda x: x['short_name'])
        self.callback_data[suite_name] = callbacks
        self.suite_data[suite_name] = suite
        self.suite_names.append(suite_name)

        print("  %s (%d tests)" % (suite_name, tests_in_suite))

