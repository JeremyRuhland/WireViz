#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse
import os
from pathlib import Path
import sys
from typing import Any, Tuple

import yaml

if __name__ == '__main__':
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from wireviz import __version__
from wireviz.DataClasses import Metadata, Options, Tweak
from wireviz.Harness import Harness
from wireviz.wv_helper import expand, open_file_read


def parse(yaml_input: str, file_out: (str, Path) = None, return_types: (None, str, Tuple[str]) = None) -> Any:
    """
    Parses yaml input string and does the high-level harness conversion

    :param yaml_input: a string containing the yaml input data
    :param file_out:
    :param return_types: if None, then returns None; if the value is a string, then a
        corresponding data format will be returned; if the value is a tuple of strings,
        then for every valid format in the `return_types` tuple, another return type
        will be generated and returned in the same order; currently supports:
         - "png" - will return the PNG data
         - "svg" - will return the SVG data
         - "harness" - will return the `Harness` instance
    """

    yaml_data = yaml.safe_load(yaml_input)

    harness = Harness(
        metadata = Metadata(**yaml_data.get('metadata', {})),
        options = Options(**yaml_data.get('options', {})),
        tweak = Tweak(**yaml_data.get('tweak', {})),
    )
    if 'title' not in harness.metadata:
        harness.metadata['title'] = Path(file_out).stem

    # add items
    sections = ['connectors', 'cables', 'connections']
    types = [dict, dict, list]
    for sec, ty in zip(sections, types):
        if sec in yaml_data and type(yaml_data[sec]) == ty:
            if len(yaml_data[sec]) > 0:
                if ty == dict:
                    for key, attribs in yaml_data[sec].items():
                        # The Image dataclass might need to open an image file with a relative path.
                        image = attribs.get('image')
                        if isinstance(image, dict):
                            image['gv_dir'] = Path(file_out if file_out else '').parent # Inject context

                        if sec == 'connectors':
                            if not attribs.get('autogenerate', False):
                                harness.add_connector(name=key, **attribs)
                        elif sec == 'cables':
                            harness.add_cable(name=key, **attribs)
            else:
                pass  # section exists but is empty
        else:  # section does not exist, create empty section
            if ty == dict:
                yaml_data[sec] = {}
            elif ty == list:
                yaml_data[sec] = []

    # add connections

    def check_designators(what, where): # helper function
        for i, x in enumerate(what):
            if x not in yaml_data[where[i]]:
                return False
        return True

    autogenerated_ids = {}
    for connection in yaml_data['connections']:
        # find first component (potentially nested inside list or dict)
        first_item = connection[0]
        if isinstance(first_item, list):
            first_item = first_item[0]
        elif isinstance(first_item, dict):
            first_item = list(first_item.keys())[0]
        elif isinstance(first_item, str):
            pass

        # check which section the first item belongs to
        alternating_sections = ['connectors','cables']
        for index, section in enumerate(alternating_sections):
            if first_item in yaml_data[section]:
                expected_index = index
                break
        else:
            raise Exception('First item not found anywhere.')
        expected_index = 1 - expected_index  # flip once since it is flipped back at the *beginning* of every loop

        # check that all iterable items (lists and dicts) are the same length
        # and that they are alternating between connectors and cables/bundles, starting with either
        itemcount = None
        for item in connection:
            expected_index = 1 - expected_index  # make sure items alternate between connectors and cables
            expected_section = alternating_sections[expected_index]
            if isinstance(item, list):
                itemcount_new = len(item)
                for subitem in item:
                    if not subitem in yaml_data[expected_section]:
                        raise Exception(f'{subitem} is not in {expected_section}')
            elif isinstance(item, dict):
                if len(item.keys()) != 1:
                    raise Exception('Dicts may contain only one key here!')
                itemcount_new = len(expand(list(item.values())[0]))
                subitem = list(item.keys())[0]
                if not subitem in yaml_data[expected_section]:
                    raise Exception(f'{subitem} is not in {expected_section}')
            elif isinstance(item, str):
                if not item in yaml_data[expected_section]:
                    raise Exception(f'{item} is not in {expected_section}')
                continue
            if itemcount is not None and itemcount_new != itemcount:
                raise Exception('All lists and dict lists must be the same length!')
            itemcount = itemcount_new
        if itemcount is None:
            raise Exception('No item revealed the number of connections to make!')

        # populate connection list
        connection_list = []
        for i, item in enumerate(connection):
            if isinstance(item, str):  # one single-pin component was specified
                sublist = []
                for i in range(1, itemcount + 1):
                    if yaml_data['connectors'][item].get('autogenerate'):
                        autogenerated_ids[item] = autogenerated_ids.get(item, 0) + 1
                        new_id = f'_{item}_{autogenerated_ids[item]}'
                        harness.add_connector(new_id, **yaml_data['connectors'][item])
                        sublist.append([new_id, 1])
                    else:
                        sublist.append([item, 1])
                connection_list.append(sublist)
            elif isinstance(item, list):  # a list of single-pin components were specified
                sublist = []
                for subitem in item:
                    if yaml_data['connectors'][subitem].get('autogenerate'):
                        autogenerated_ids[subitem] = autogenerated_ids.get(subitem, 0) + 1
                        new_id = f'_{subitem}_{autogenerated_ids[subitem]}'
                        harness.add_connector(new_id, **yaml_data['connectors'][subitem])
                        sublist.append([new_id, 1])
                    else:
                        sublist.append([subitem, 1])
                connection_list.append(sublist)
            elif isinstance(item, dict):  # a component with multiple pins was specified
                sublist = []
                id = list(item.keys())[0]
                pins = expand(list(item.values())[0])
                for pin in pins:
                    sublist.append([id, pin])
                connection_list.append(sublist)
            else:
                raise Exception('Unexpected item in connection list')

        # actually connect components using connection list
        for i, item in enumerate(connection_list):
            id = item[0][0]  # TODO: make more elegant/robust/pythonic
            if id in harness.cables:
                for j, con in enumerate(item):
                    if i == 0:  # list started with a cable, no connector to join on left side
                        from_name = None
                        from_pin  = None
                    else:
                        from_name = connection_list[i-1][j][0]
                        from_pin  = connection_list[i-1][j][1]
                    via_name  = item[j][0]
                    via_pin   = item[j][1]
                    if i == len(connection_list) - 1:  # list ends with a cable, no connector to join on right side
                        to_name   = None
                        to_pin    = None
                    else:
                        to_name   = connection_list[i+1][j][0]
                        to_pin    = connection_list[i+1][j][1]
                    harness.connect(from_name, from_pin, via_name, via_pin, to_name, to_pin)

    if "additional_bom_items" in yaml_data:
        for line in yaml_data["additional_bom_items"]:
            harness.add_bom_item(line)

    if file_out is not None:
        harness.output(filename=file_out, fmt=('png', 'svg'), view=False)

    if return_types is not None:
        returns = []
        if isinstance(return_types, str): # only one return type speficied
            return_types = [return_types]

        return_types = [t.lower() for t in return_types]

        for rt in return_types:
            if rt == 'png':
                returns.append(harness.png)
            if rt == 'svg':
                returns.append(harness.svg)
            if rt == 'harness':
                returns.append(harness)

        return tuple(returns) if len(returns) != 1 else returns[0]


def parse_file(yaml_file: str, file_out: (str, Path) = None) -> None:
    with open_file_read(yaml_file) as file:
        yaml_input = file.read()

    if not file_out:
        fn, fext = os.path.splitext(yaml_file)
        file_out = fn
    file_out = os.path.abspath(file_out)

    parse(yaml_input, file_out=file_out)


def parse_cmdline():
    parser = argparse.ArgumentParser(
        description='Generate cable and wiring harness documentation from YAML descriptions',
    )
    parser.add_argument('-V', '--version', action='version', version='%(prog)s ' + __version__)
    parser.add_argument('input_file', action='store', type=str, metavar='YAML_FILE')
    parser.add_argument('-o', '--output_file', action='store', type=str, metavar='OUTPUT')
    # Not implemented: parser.add_argument('--generate-bom', action='store_true', default=True)
    parser.add_argument('--prepend-file', action='store', type=str, metavar='YAML_FILE')
    return parser.parse_args()


def main():

    args = parse_cmdline()

    if not os.path.exists(args.input_file):
        print(f'Error: input file {args.input_file} inaccessible or does not exist, check path')
        sys.exit(1)

    with open_file_read(args.input_file) as fh:
        yaml_input = fh.read()

    if args.prepend_file:
        if not os.path.exists(args.prepend_file):
            print(f'Error: prepend input file {args.prepend_file} inaccessible or does not exist, check path')
            sys.exit(1)
        with open_file_read(args.prepend_file) as fh:
            prepend = fh.read()
            yaml_input = prepend + yaml_input

    if not args.output_file:
        file_out = args.input_file
        pre, _ = os.path.splitext(file_out)
        file_out = pre  # extension will be added by graphviz output function
    else:
        file_out = args.output_file
    file_out = os.path.abspath(file_out)

    parse(yaml_input, file_out=file_out)


if __name__ == '__main__':
    main()
