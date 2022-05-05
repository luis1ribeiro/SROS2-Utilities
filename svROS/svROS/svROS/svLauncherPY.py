import os, argparse, time, shutil, glob, warnings, logging, re, sys, subprocess
from yaml import *
from dataclasses import dataclass, field
from logging import FileHandler
from collections import defaultdict

# Parsers
from lxml import etree
from lark import Lark, tree
# haros...
from haros.haros import HarosExportRunner
# haros launcher is deprecated... => However, this tool makes use of the knowledge explored by them...
# from haros.launch_parser import BaseLaunchTag, IncludeTag, RemapTag

# InfoHandler => Prints, Exceptions and Warnings
from tools.InfoHandler import color, svROS_Exception as excp, svROS_Info as info
from tools.Loader import Loader

# python parser helper...
from bonsai.model import (
    CodeGlobalScope, CodeReference, CodeFunctionCall, pretty_str
)
from bonsai.analysis import (
    CodeQuery, resolve_reference, resolve_expression, get_control_depth,
    get_conditions, get_condition_paths, is_under_loop
)
from bonsai.py.py_parser import PyAstParser

global WORKDIR, SCHEMAS
WORKDIR = os.path.dirname(__file__)
SCHEMAS = os.path.join(WORKDIR, '../schemas/')

"Functions that every class inherits"
class BaseCall(object):

    @staticmethod
    def get_value(call):
        return call.named_args[0].value

    @staticmethod
    def process_code_reference(call):
        reference = resolve_reference(call)
        if reference == None:
            raise
        return reference

    """ === Static Methods === """

"Reference through call of LaunchConfiguration"
class ReferenceCall(BaseCall):
    REQUIRED = ("value")
    """
    ...
        \_ LaunchConfiguration ==> ReferenceCall
    """

    def __init__(self, name):
        # initial configuration
        self.name = name
        self.referenced = []

    def _add_reference(self, reference):
        self.referenced.append(reference)
        return True


class ArgsCall(BaseCall):
    # ARGS class variables
    CALL_REFERENCES = {}
    ARGS            = {}
    REQUIRED = ("name", r"(default_value|value)")
    """
        DeclareLaunchArgument/SetEnvironmentVariable
            \__ name
            \__ named_args
            \__ arguments => TextSubstitution
                                \_ named_args
                                \_ LaunchConfiguration ==> ReferenceCall
    """

    def __init__(self, name, value):
        # initial configuration
        self.name   = name
        self.value  = value
        ArgsCall.ARGS[self.name] = self

    @staticmethod
    def process_references(name, value):
        if name in ArgsCall.CALL_REFERENCES:
            reference = ArgsCall.CALL_REFERENCES[name]
            for ref in reference.referenced:
                ref.value = value
        return True

    @staticmethod
    def process_argument(call=None):
        name  = call.arguments[0]
        value = ArgsCall.get_value(call=call)

        if isinstance(value, str):
            ArgsCall.process_references(name, value)
            return ArgsCall.init_argument(name=name, value=value)
        else:
            # Parsing-Error processing...
            process_argument = ArgsCall.process_argument_value(name=name, value=value, tag=value.name)
            if process_argument == '':
                return None
            value = process_argument

        if isinstance(value, ReferenceCall):
            if value.name in ArgsCall.ARGS:
                arg_reference_value = ArgsCall.ARGS[value.name].value
                return ArgsCall.init_argument(name=name, value=arg_reference_value)
            
            reference = ArgsCall.CALL_REFERENCES[value.name]
            arg       = ArgsCall.init_argument(name=name, value=value)
            reference._add_reference(arg)
            return arg
        else:
            # TextSubstitution
            ArgsCall.process_references(name, value)
            return ArgsCall.init_argument(name=name, value=value)

    @staticmethod
    def process_argument_value(name, value, tag):
        VALID_VALUE_TAGS = {'LaunchConfiguration', 'TextSubstitution'}
        
        if tag not in VALID_VALUE_TAGS:
            return ''
        # Text substitution is clear.
        if tag == 'TextSubstitution':
            value = ArgsCall.get_value(call=value)
        # LaunchConfiguration might be different.
        else:
            value = value.arguments[0]
            if not value in ArgsCall.CALL_REFERENCES:
                # else, create another reference call
                value = ReferenceCall(name=value)
                ArgsCall.CALL_REFERENCES[value.name] = value
        
        if isinstance(value, CodeReference):
            value = ArgsCall.process_code_reference(value)

        return value

    @classmethod
    def init_argument(cls, name, value):
        return cls(name=name, value=value)


class RemapCall(BaseCall):
    # class variables
    REQUIRED = ("from", "to")
    REMAPS = {}

    def __init__(self, f, t):
        self.origin = f
        self.destin = t

        RemapCall.REMAPS.add(self)
        
    @classmethod
    def init_remap(cls, **kwargs):
        return cls(f=kwargs.get('from'), t=kwargs.get('to'))


class NodeCall(BaseCall):
    # NODES class variables
    NODES          = {}
    PACKAGES_NODES = {}
    CHILDREN = ("remap", "param")
    REQUIRED = ("package", "executable", "name")
    """
        Node
            \__ named_args
                    \_ Text
                    \_ TextSubstitution
                    \_ LaunchConfiguration ==> ReferenceCall
    """

    def __init__(self, name, package, executable, remaps, namespace=None, enclave=None):
        # initial configuration
        self.name       = name
        self.namespace  = namespace
        self.package    = package
        self.executable = executable
        self.remaps     = remaps
        self.enclave    = enclave

        NodeCall.NODES[self.name] = self
        if self.package in NodeCall.PACKAGES_NODES: NodeCall.PACKAGES_NODES[self.package].add(self)
        else: NodeCall.PACKAGES_NODES[self.package] = {self}

    @classmethod
    def init_node(cls, **kwargs):
        return cls(name=kwargs['name'], package=kwargs['package'], executable=kwargs['executable'], remaps=kwargs['remaps'], namespace=kwargs.get('namespace'), enclave=kwargs.get('enclave'))

    @staticmethod
    def process_cmd_arg(info_data, tag, enclave=False):
        # processing grammar...
        data = list(tree.find_data(info_data))
        output = []
        _data_ = data[len(data)-1]
        if _data_:
            # get tokens
            output = list(map(lambda v: v.value , list(filter(lambda vv: vv.type == tag, (_data_.scan_values(lambda v: isinstance(v, Token)))))))
            if enclave == False:
                output = list(zip(output[0::2], output[1::2]))
            
        return output
    
    # Using lark to parse some dependencies strings...
    @staticmethod
    def parse_cmd_args(args=''):
        # returning dictionary
        output = {}
        output['remaps'] = list()
            
        # Grammar to parse arguments...
        grammar = """
            sentence: INIT complete?
            complete: REMAP /\s/ arg_remap
                    | ENCLAVE /\s/ arg_enclave
                    | PARAMETER /\s/ arg_parameter

            arg_remap: ARG_R ":=" ARG_R (/\s/ (arg_remap | complete))*
            arg_enclave: ARG_E (/\s/ complete)*
            arg_parameter: ARG_P ":=" ARG_P (/\s/ (arg_parameter | complete))*

            INIT: "--ros-args"
            REMAP:"--remap" | "-r"
            ENCLAVE: "--enclave" | "-e"
            PARAMETER: "--parameter" | "-p"

            ARG_R:/(?!\:\=)[a-zA-Z0-9_\/\-.]+/
            ARG_E:/(?!\s)[a-zA-Z0-9_\/\-.]+/
            ARG_P:/(?!\:\=)[a-zA-Z0-9_\/\-.]+/

            %import common.WS
            %ignore WS
        """
        # Larker parser
        parser = Lark(grammar, start='sentence', ambiguity='explicit')
        tree = parser.parse(f'{args}')

        remaps  = BaseCall.process_cmd_arg(info_data="arg_remap", token='ARG_R')
        enclave = BaseCall.process_cmd_arg(info_data="arg_enclave", token='ARG_E', enclave=True)[0]

        for pair in remaps:
            output['remaps'].append({'from': str(pair[0]), 'to': str(pair[1])})

        output['enclave'] = str(enclave)

        return output

    @staticmethod
    def process_argument_value(name, value, tag):
        VALID_VALUE_TAGS = {'LaunchConfiguration', 'TextSubstitution'}
        # literal is related to list => for remappings and 
        
        if tag not in VALID_VALUE_TAGS:
            return ''
        # Text substitution is clear.
        if tag == 'TextSubstitution':
            value = NodeCall.get_value(call=value)
        # LaunchConfiguration might be different.
        elif tag == 'LaunchConfiguration':
            value = value.arguments[0]
            # Reference to argument already processed
            if value not in ArgsCall.ARGS:
                raise
            value = ArgsCall.ARGS[value].value
        else:
            raise

        if isinstance(value, CodeReference):
            value = NodeCall.process_code_reference(value)
        
        return value

    @staticmethod
    def process_node_argument(value, tag=None):

        if isinstance(value, str):
            return value
        else:
            # Parsing-Error processing...
            process_argument = NodeCall.process_argument_value(value=value, tag=value.name)
            if process_argument == '':
                return None
            value = process_argument

        return value

    @staticmethod
    def process_cmd_args(values):
        arguments_cmd = ''
        for v in values:
            if isinstance(v, str):
                arg_cnd = v
            else:
                arg_cmd = NodeCall.process_node_argument(value=v.value, tag=v.name)
            arguments_cmd += arg_cmd
        output = NodeCall.parse_cmd_args(args=arguments_cmd)
        return output.get('enclave'), output.get('remaps')

    @staticmethod
    def process_remaps(values):
        remaps = []
        for v in values:
            if isinstance(v.value[0], str):
                pass
            else:
                _from = NodeCall.process_node_argument(value=v.value[0].value, tag=v.value[0].name)
            if isinstance(v.value[1], str):
                pass
            else:
                _to = NodeCall.process_node_argument(value=v.value[1].value, tag=v.value[1].name)
            
            object_remap = {'from': _from, 'to': _to}
            remaps.append(object_remap)
        return remaps

    @staticmethod
    def process_node_arguments(arguments):
        """
        Node args to be processed:
            \_ name
            \_ package
            \_ executable
            \_ namespace (?)
            \_ remaps and arguments
        """
        VALID_NODE_ARGUMENTS = {
            'name', 'package', 'executable', 'namespace'
        }
        REMAPPINGS  = {
            'remappings'
        }
        ARGUMENTS   = {
            'arguments'
        }

        node_arguments = {}
        node_arguments['remaps'] = []
        base_arguments = list(filter(lambda arg: arg.name in VALID_NODE_ARGUMENTS, arguments))
        for arg in arguments:
            if arg.name in VALID_NODE_ARGUMENTS:
                node_arguments[arg.name] = NodeCall.process_node_argument(value=arg.value, tag=arg.name)
            elif arg.name in ARGUMENTS:
                # in-line cmd ros2 arguments
                cmd_enclave, cmd_remaps = NodeCall.process_cmd_args(values=arg.value.value)
                node_arguments['enclave'] = cmd_enclave
                for remap in cmd_remaps:
                    node_arguments['remaps'].append(remap)
                    RemapCall.init_remap(remap)
            elif arg.name in REMAPPINGS:
                remaps = NodeCall.process_remaps(remaps=arg.value.value)
                for remap in remaps:
                    node_arguments['remaps'].append(remap)
                    RemapCall.init_remap(remap)

        return node_arguments


    @staticmethod
    def process_node(call=None):
        # in-line arguments
        inline_arguments = call.named_args
        arguments        = call.arguments
        
        if arguments:
            raise
            return None 
        
        node_arguments = NodeCall.process_node_arguments(arguments=inline_arguments)
        NodeCall.init_node(**node_arguments)

    @property
    def name(self):
        if self.namespace == None:
            return self.name
        else:
            return self.namespace + '/' + self.name


""" 
    This file contains the necessary classes and methods to export information from the launch file Python-based specified within the config file.

    SCHEMA that ros2 provides is deprecated also... So we'll try to run ros2 launch -p instead:
        => ros2 launch $file -p :: 

    ROS2 launch is based on python, which I, Luís Ribeiro, test the tool in order to try to retrive some useful structures to ease the parsing process, however, they do not furnish a direct way of acessing those structures. Therefore, this parsing technique might have some attached issues.
"""

"Launcher parser in order to retrieve information about possible executables..."
@dataclass
class LauncherParserPY:

    """ TAGS:
        . Node tag                  -> Reference to a node
        . DeclareLaunchArgument tag -> Arguments that can be used inside a node
        . LaunchConfiguration   tag -> Yet more arguments...
    """
    # Call-based tags
    TAGS = {
        "Base": BaseCall,
        "Node": NodeCall, # underlying remap call
        "Remap": RemapCall,
        "DeclareLaunchArgument" : {ArgsCall, ReferenceCall},
        "SetEnvironmentVariable": {ArgsCall, ReferenceCall}

    }
    file      : str

    """ === Predefined functions === """
    # validate xml schema
    @staticmethod
    def validate_schema(file, execute_cmd=(False,'')):

        # Due to the depecrated xml file, the user might opt to check syntax through execution commands
        if execute_cmd[0] == True:
            cmd = execute_cmd[1].split(' ')
            try:
                subprocess.check_call(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
            except Exception as error:
                return False
        return True
    

    # validate py schema
    @staticmethod
    def validate_py_schema(file, schema, workspace):
        # PYTHON tags that should be evaluated...
        """ TAGS:
                . Node tag                  -> Reference to a node
                . DeclareLaunchArgument tag -> Arguments that can be used inside a node
                . LaunchConfiguration   tag -> Yet more arguments...
        """
        VALID_TAGS = {
            'Node': [],
            'DeclareLaunchArgument': [],
            'SetEnvironmentVariable': []
        }

        # no workspace found...
        if workspace == '':
            return {}, None
        try:
            parser = PyAstParser(workspace=workspace)
            parser.parse(file)
        except:
            return {}, None

        # parser global scope
        __gs__ = parser.global_scope

        # fill with calls
        VALID_TAGS = {tag: CodeQuery(__gs__).all_calls.where_name(tag).get() for tag in VALID_TAGS}
        # to fulfill...
        CALLABLE_TAGS = {}

        ### GET VALID CALLABLE FUNCTIONS ###
        callable_functions = str((CodeQuery(__gs__).all_calls.where_name('LaunchDescription').get())[0].arguments)
        for _callable in re.findall(r'\#(.*?)[\,\}]\s*',callable_functions): 
            scope = CodeQuery(__gs__).all_definitions.where_name(_callable).get()[0].scope
            
            call, name = re.findall(r'\=.*?\]\s*(.*?)\((.*?)\)\s*$',str(scope))[0]
            call = str(call).strip()
            name = str(name).strip()
            
            # tag validation...
            if call in VALID_TAGS:
                # treatment
                if name == '':
                        name = str(_callable).strip()
                        CALLABLE_TAGS[str(_callable).strip()] = call  

                call = list(filter(lambda n_call: str(_callable).strip() == str(n_call.parent.arguments[0].name.strip()), VALID_TAGS[call]))[0]
                CALLABLE_TAGS[name] = call

        # Schema routines...
        return CALLABLE_TAGS, __gs__

    # validate py schema
    @staticmethod
    def launch_py(calls={}, __gs__=None):

        ARGS_TAGS={
            'DeclareLaunchArgument',
            'SetEnvironmentVariable'
        }
        # handling possible errors...
        if calls == {} or parser is None:
            return {}
        # returning obj :: initialize...
        object_ret = {}

        arguments = list(filter(lambda call: calls[call].name == 'DeclareLaunchArgument', calls))
        envs      = list(filter(lambda call: calls[call].name == 'SetEnvironmentVariable', calls))
        nodes     = list(filter(lambda call: calls[call].name == 'Node', calls))

        # Processing...
        for arg in arguments: ArgsCall.process_argument(call=calls[arg])
        for env in envs     : ArgsCall.process_argument(call=calls[env])

        for node in nodes   : NodeCall.process_node(call=calls[node])


        for ref_name in calls:
            element = calls[ref_name]
            if calls[call_name] != {}:
                codeQ_calls = CodeQuery(__gs__).all_calls.where_name(call_name).get()



    # parse py launcher
    def parse(self, filename):
        # Warner the user first...
        print(f'[svROS] {color.color("BOLD", color.color("YELLOW", "WARNING:"))} Python Launch file parser might be deprecated due to complexity analysis...')
        time.sleep(0.5)

        # validate schema first...
        if not LauncherParserPY.validate_schema(file=filename, execute_cmd=(True,f'ros2 launch {filename} -p')):
            return False

        # validate schema first...
        # Note that workspace for ros launch packages must be given...
        CALLABLE_TAGS = LauncherParserPY.validate_py_schema(file=f, workspace=LauncherParserPY.get_launch_python_workspace())
        if CALLABLE_TAGS[0] == {}:
            return False
        
        global_scope    = CALLABLE_TAGS[1]
        CALLABLE_TAGS   = CALLABLE_TAGS[0]
        # Here the idea is to capture all the possible tags that python launch might have
        object_ret = LauncherParserPY.launch_py(calls=CALLABLE_TAGS, __gs__=global_scope)
        if object_ret == {}:
            return False

        return True
    
    # Python workspace getter so that can parse launch file with python extension...
    @staticmethod
    def get_launch_python_workspace():
        ros_distro = os.getenv('ROS_DISTRO')
        locate = f'/opt/ros2/{ros_distro}/lib'

        workspace_path = os.getenv('PYTHONPATH').split(':')[::-1][0]
        if not workspace_path.startswith(f'{locate}'):
            return ''
        return workspace_path

    # get positional
    @staticmethod
    def decouple(structure):
        if structure is not None:
            return structure[0]

    """ === Predefined functions === """

# Testing...
if __name__ == "__main__":
    file = sys.argv[1]

    l = LauncherParserPY(file=file)
    conf = l.parse()
    print(conf.nodes)