import os, argparse, time, shutil, glob, warnings, logging, re, sys, subprocess
from yaml import *
from dataclasses import dataclass, field
from logging import FileHandler
from typing import ClassVar
# InfoHandler => Prints, Exceptions and Warnings
from tools.InfoHandler import color, svException, svWarning
# Parsers
import xml.etree.ElementTree as ET
from lark import Lark, tree

global WORKDIR, SCHEMAS
WORKDIR = os.path.dirname(__file__)
SCHEMAS = os.path.join(WORKDIR, '../schemas/')

""" 
    This file contains the necessary classes and methods to store and retrieve information about the ros2 running environment => NODES, TOPICS AND PACKAGES INVOLVED.
"""
"ROS2-based Package class."
class Package:
    PACKAGES = set()
    """
        Packages
            \_ Valid nodes from packages
    """
    def __init__(self, name: str, path: str, nodes: dict):
        self.name, self.path, self.nodes = name, path, nodes
        Package.PACKAGES.add(self)

    @classmethod
    def init_package_name(cls, name, index):
        # Only clear if its the first package.
        if index == 0: cls.PACKAGES.clear()
        return cls(name=name, path='', nodes=None)

"ROS2-based for message topic_type as this tool focus on Topic-Message processing."
class MessageType(object):
    TYPES  = {}
    VALUES = set()
    def __init__(self, name, signature, topic):
        self.name, self.signature, self.topics, self.values = name, signature, set(), set()
        self.topics.add(topic)
        MessageType.TYPES[name]   = self

    @classmethod
    def init_message_type(cls, name, signature, topic):
        if name in cls.TYPES:
            _type = cls.TYPES[name]
            _type.topics.add(topic)
            return _type
        return cls(name=name, signature=signature, topic=topic)
    
    @staticmethod
    def abstract(tag): 
        tag = tag.lower().replace('/', '_')
        return tag.lower()[(tag.rfind('_'))+1:].capitalize()
    
    def __str__(self):
        # {{\n\tvalue in {self.value.signature}\n}}
        _str_ = f"""sig {self.signature} extends Message {{}}"""
        values, isdigit = set(), False
        for v in self.values:
            if not v.lstrip("-").isdigit():
                MessageType.VALUES.add(v)
            else:
                isdigit = True
            values.add(v)
        if isdigit:
            _str_ += f"""\nfact {{ {self.signature} in {'+'.join(values)} + Int {{}} }}\n"""
        else:
            _str_ += f"""\nfact {{ {self.signature} in {'+'.join(values)} }}\n"""
        return _str_

"ROS2-based Topic already parse for node handling."
class Topic(object):
    TOPICS   = {}
    """
        Topic
            \__ Name
            \__ Type
    """
    def __init__(self, name, topic_type, message_type=None):
        self.name, self.type, self.remap, self.signature, self.message_type = name, topic_type, None, 'topic'+self.abstract(tag=name), message_type
        Topic.TOPICS[name] = self
        
    @classmethod
    def init_topic(cls, name, topic_type):
        if name in Topic.TOPICS:
            topic = Topic.TOPICS[name]
            if topic_type != topic.type: raise svException(f'Same topic ({name}) with different types ({topic_type, topic.type}).')
            else: return topic
        message_type = MessageType.init_message_type(name=topic_type.lower().replace('/', '_'), signature=MessageType.abstract(tag=topic_type), topic=name.lower().replace('/', '_'))
        return cls(name=name, topic_type=topic_type, message_type=message_type)

    @staticmethod
    def namespace(tag: str):
        return tag if tag.startswith('/') else f'/{tag}'
    
    def rosname(self, node):
        if node and node.namespace:
            rosname = Topic.namespace(tag=node.namespace)
            if self.remap: rosname += Topic.namespace(tag=self.remap)
            else:          rosname += Topic.namespace(tag=self.name)
        else:
            if self.remap: rosname = Topic.namespace(tag=self.remap)
            else:          rosname = Topic.namespace(tag=self.name)
        return rosname

    def abstract(self, tag): return tag.lower().replace('/', '_')

    def declaration(self):
        abstract_type, self.message_type = self.abstract(tag=self.type), MessageType.TYPES[self.abstract(tag=self.type).lower()]
        return f"""one sig {self.signature} extends Topic {{}}\n"""
        #"""{{(box0 + box1) in {self.message_type.signature}}}\n"""

    @classmethod
    def topic_declaration(cls):
        TOPICS       = cls.TOPICS
        declaration  = ''.join(list(map(lambda topic: TOPICS[topic].declaration(), TOPICS))) + '\n'
        # CHANNEL COHENRECY
        # declaration  += f"""fact type_coherency {{\n\talways ("""
        # temp          = []
        # for topic in TOPICS:
        #    topic = TOPICS[topic]
        #    temp.append(f"""elems[{topic.signature}.(Trace.inbox)] in {topic.message_type.signature}""")
        # declaration  += ' and '.join(temp) + f""")\n}}\n\n"""
        # VALUES
        VALUES       = MessageType.VALUES
        for v in VALUES:
            declaration += f'\none sig {self.signature} extends Msg'
        # TYPES
        TYPES        = MessageType.TYPES
        declaration += '\n'.join(list(map(lambda msgtp: str(TYPES[msgtp]) , TYPES )))
        return declaration

    @classmethod
    def list_of_types(cls):
        # Returning object.
        ret_object = {}
        for index in cls.TOPICS:
            topic = cls.TOPICS[index]
            if topic.type not in ret_object:
                ret_object[topic.type] = None
        return ret_object

"ROS2-based Node already parse, with remaps and topic handling."
class Node(object):
    NODES = {}
    """
        Node
            \__ INFO FROM NODETAG OR NODECALL
            \__ Topic subscribing and publishing
    """
    def __init__(self, name, namespace, package, executable, remaps, enclave=None):
        self.name, self.namespace, self.package, self.executable, self.remaps, self.enclave  = name, namespace, package, executable, remaps, enclave
        # Associated source file.
        self.source     = None
        # Add to NODES class variable.
        index = self.index
        Node.NODES[index] = self

    # Update node with its Source and Topic Handling
    def store_node_source(self, source):
        self.source, self.subscribes, self.publishes = source.name, source.subscribes, source.publishes

    @classmethod
    def process_config_file(cls):
        # Returning object.
        nodes, topics = {}, {}, {}
        for index in cls.NODES:
            node  = cls.NODES[index]
            nodes[index]               = {}
            nodes[index]['rosname']    = node.rosname
            nodes[index]['enclave']    = str(node.enclave) if node.enclave else '/public'
            # Behaviour
            nodes[index]['behaviour']  = ['']
            # Topic treatment.
            for adv in node.publishes:
                topic_type   = adv.type
                name         = Node.render_remap(topic=adv, remaps=node.remaps).rosname(node=node)
                topics[name] = topic_type
            for sub in node.subscribes:
                topic_type   = sub.type
                name         = Node.render_remap(topic=sub, remaps=node.remaps).rosname(node=node)
                topics[name] = topic_type
        return nodes, topics

    @classmethod
    def list_of_nodes(cls):
        # Returning object.
        ret_object = {}
        for index in cls.NODES:
            ret_object[index] = [None]
        return ret_object

    @classmethod
    def process_sros_file(cls, template):
        enclaves = template.find(f'.//enclaves')
        for index in cls.NODES:
            node = cls.NODES[index]
            # Process Enclave
            if node.enclave is not None:
                enclave = None
                for en in enclaves:
                    if en.get('path') == str(node.enclave):
                        enclave  = en
                        profiles = enclave[0]
                        break
                if enclave is None:
                    enclave  = ET.Element('enclave')
                    enclave.set('path', str(node.enclave))
                    profiles = ET.Element('profiles')
                    enclave.append(profiles)
                    enclaves.append(enclave)
            else:
                enclave = None
                for en in enclaves:
                    if en.get('path') == '/public':
                        enclave  = en
                        profiles = enclave[0]
                        break
                if enclave is None:
                    enclave  = ET.Element('enclave')
                    # enclave.set('path', str(node.enclave))
                    enclave.set('path', '/public')
                    profiles = ET.Element('profiles')
                    enclave.append(profiles)
                    enclaves.append(enclave)
            # Process Node
            profile = ET.Element('profile')
            if node.namespace: profile.set('ns', Node.namespace(tag=node.namespace) + '/')
            else: profile.set('ns', '/')
            profile.set('node', node.name)
            # Process Topic Publish
            pubs = ET.Element('topics')
            pubs.set('publish', 'ALLOW')
            pubs_names = []
            for adv in node.publishes:
                topic = Node.render_remap(topic=adv, remaps=node.remaps)
                if topic.remap: name = topic.remap 
                else:           name = topic.name
                if name in pubs_names: continue
                pubs_names.append(name)
                topic      = ET.Element('topic')
                topic.text = str(name) if not name.startswith('/') else str(name[1:])
                pubs.append(topic)
            # Process Topic Subscribe
            subs = ET.Element('topics')
            subs.set('subscribe', 'ALLOW')
            subs_names = []
            for sub in node.subscribes:
                topic = Node.render_remap(topic=sub, remaps=node.remaps)
                if topic.remap: name = topic.remap 
                else:           name = topic.name
                if name in subs_names: continue
                subs_names.append(name)
                topic      = ET.Element('topic')
                topic.text = str(name) if not name.startswith('/') else str(name[1:])
                subs.append(topic)
            # Processing Profile
            if subs.findall('./') != []: profile.append(subs)
            if pubs.findall('./') != []: profile.append(pubs)
            profiles.append(profile)
        # Retrieve XML
        return template

    # Retrieve JSON-based dict information
    @staticmethod
    def to_json(node: str):
        node   = Node.NODES[node]
        # Enclave is not needed at this point
        topics = {'subscribe': list(map(lambda subs: subs.rosname(node=node), node.subscribes)), 'advertise': list(map(lambda pubs: pubs.rosname(node=node), node.publishes)), 'remaps': node.remaps}
        return {'package': node.package, 'executable': node.executable, 'namespace': node.namespace, 'rosname': node.rosname, 'topics': topics}

    @staticmethod
    def render_remap(topic, remaps):
        name = Topic.namespace(tag=topic.name)
        for r in remaps:
            if name.strip() == r['from'].strip():
                name        =  r['to'].strip()
                topic.remap = name
                break
        return topic

    @staticmethod
    def namespace(tag: str):
        return tag if tag.startswith('/') else f'/{tag}'
    
    @classmethod
    def init_node(cls, **kwargs):
        return cls(name=kwargs['_name'], namespace=kwargs['namespace'], package=kwargs['package'], executable=kwargs['executable'], remaps=Node.process_remaps(kwargs['remaps']), enclave=kwargs.get('enclave'))

    @staticmethod
    def process_remaps(remaps: list):
        # Snub list
        remap_temp = dict()
        for remap in remaps:
            remap_temp[remap.get('from')] = remap.get('to')
        # Iter through temp dict
        for remap in remap_temp:
            value = remap_temp[remap]
            value = Node.process_remap_value(remap=remap, value=value, remaps=remap_temp)
            # Process after getting new value
            if remap == value:
                del remap_temp[remap]
                continue
            remap_temp[remap] = value
        return list(map(lambda remap: {'from': remap, 'to': remap_temp[remap]}, remap_temp))

    @staticmethod
    def process_remap_value(remap, value, remaps):
        if value in remaps:
            current_value = remaps[value]
            if list(remaps.keys()).index(value) > list(remaps.keys()).index(remap):
                value = Node.process_remap_value(value, current_value, remaps)
        return value

    @property
    def rosname(self):
        if self.namespace: rsn_ = self.namespace + '/' + self.name
        else: rsn_ = self.name
        return Node.namespace(tag=rsn_)

    @property
    def index(self):
        if self.namespace: index_ = self.package + '/' + self.namespace + '/' + self.name
        else: index_ = self.package + '/' + self.name
        return index_

""" 
    The remaining of this file contains the necessary classes and methods to retrieve and use information for analysis purposes. 
"""
"ROS2-based Node to be analyzed."
class svROSNode(object):
    NODES        = {}
    OBSDT        = {}
    # Set of channels that are published by private but seen as public
    PUBSYNC      = set()
    OBSERVATIONS = set()
    """
        svROSNode
            \__ Already parsed node
            \__ Associated with Profile from SROS (can either be secured or unsecured)
    """
    def __init__(self, full_name, profile, **kwargs):
        self.index, self.rosname, self.namespace, self.executable, self.profile, self.predicate = full_name, kwargs.get('rosname'), kwargs.get('namespace'), kwargs.get('executable'), profile, None
        self.enclave = profile.enclave if profile else None
        # Process node-package.
        self.package = self.index.replace(self.rosname, '') 
        if self.package not in list(map(lambda pkg: pkg.name, Package.PACKAGES)):
            raise svException(f'Package {self.package} defined in node {self.index} not defined.')
        # Constrain topic allowance.
        self.subscribe, self.advertise = self.load_profile_topics()
        # GET from Pickle classes.
        if Node.NODES: self.remaps = svROSNode.load_remaps(node_name=self.index)
        # Store in class variable.
        svROSNode.NODES[self.index] = self
    
    def load_profile_topics(self):
        subs, advs = list(), list()
        if self.profile.subscribe:
            for subscribe in self.profile.subscribe:
                if subscribe not in Topic.TOPICS:
                    print(svWarning(f'Privilege {subscribe} of {self.rosname} defined in SROS file but not has no matching topic in config file!'))
                else:
                    subs.append(Topic.TOPICS[subscribe])
        if self.profile.advertise:
            for advertise in self.profile.advertise:
                if advertise not in Topic.TOPICS:
                    print(svWarning(f'Topic {advertise} of {self.rosname} of defined in SROS but not has no matching in config file!'))
                else:
                    advs.append(Topic.TOPICS[advertise])
        return subs, advs

    # Get loaded Nodes from PICKLE.
    @classmethod
    def load_remaps(cls, node_name):
        if node_name in Node.NODES:
            node = Node.NODES[node_name]
            return node.remaps
        else: return list()

    def abstract(self, tag): return tag.capitalize().replace('/', '_')

    # This method will allow to check what the output might be when an unsecured enclave publishes something from one of its topics
    @classmethod
    def observalDeterminism(cls, steps, inbox):
        if list(map(lambda node: node.connection, cls.NODES.values())) == []:
            raise svException(f'Failed to check Observable Determinism: No connections set between public and private parts.')
        if cls.OBSERVATIONS is set():
            print(svWarning('Observable Determinism is respected: No connections between private and public parts, meaning that no observations can be verified!'))
            return False
        observations = set()
        for topic in cls.OBSERVATIONS:
            if not isinstance(topic, Topic):
                raise svException(f'{topic.signature} is not a topic!')
            svROSNode.PUBSYNC.add(f"""\n\talways ((some m0 : Message | publish[T1, {topic.signature}, m0]) iff (some m1 : Message | publish[T2, {topic.signature}, m1]))""")
            observations.add(f'check {{always (all m0, m1 : Message | publish[T1, {topic.signature}, m0] and publish[T2, {topic.signature}, m1] implies m0 = m1)}} for 4 but {inbox} seq, 1..{steps} steps')
        cls.OBSERVATIONS = observations
        return True

    def set_connection(self):
        if not self.advertise: return None
        access_to = {topic.name: set() for topic in self.advertise}
        for node_name in svROSNode.NODES:
            node = svROSNode.NODES[node_name]
            if node == self or not node.subscribe: continue
            subscribes_in = list(filter(lambda sub_: sub_ in access_to, list(map(lambda sub: sub.name, node.subscribe))))
            if subscribes_in != []: 
                for sub in subscribes_in:
                    if (not node.secure and self.secure):
                        print(svWarning(f'Connection through {sub} is not well supported. {node.rosname.capitalize()} is not secure, while {self.rosname.capitalize()} is secure: {color.color("BOLD", f"{self.rosname.capitalize()} -{sub}-> {node.rosname.capitalize()}")}'))
                        svROSNode.OBSERVATIONS.add(Topic.TOPICS[sub])
                    elif (not self.secure and node.secure):
                        print(svWarning(f'Connection through {sub} is not well supported. {self.rosname.capitalize()} is not secure, while {node.rosname.capitalize()} is secure: {color.color("BOLD", f"{self.rosname.capitalize()} -{sub}-> {node.rosname.capitalize()}")}'))
                    access_to[sub].add(node)
        return access_to

    @classmethod
    def handle_connections(cls):
        if cls.NODES is {}: raise svException("No nodes found, can not process handling of connections.")
        for node_name in cls.NODES:
            node            = cls.NODES[node_name]
            node.connection = node.set_connection()

    @property
    def node_observable_determinism(self):
        return self._node_observable_determinism
        
    @node_observable_determinism.setter
    def node_observable_determinism(self, value):
        if self.secure: raise svException('You are not supposed to be here. (╯ ͡❛ ͜ʖ ͡❛)╯┻━┻')
        self._node_observable_determinism = value

    @property
    def secure(self):
        return bool(self.enclave.ispublic == False)

    def __str__(self):
        advertises = None if (self.advertise is None) else ' + '.join(list(map(lambda t: t.signature, self.advertise)))
        subscribes = None if (self.subscribe is None) else ' + '.join(list(map(lambda t: t.signature, self.subscribe)))
        if not advertises: advertises = "no advertises"
        else:              advertises = f"advertises = {advertises}"
        if not subscribes: subscribes = "no subscribes"
        else:              subscribes = f"subscribes = {subscribes}"
        declaration  = f'one sig node{self.abstract(tag=self.rosname)} extends Node {{}} {{\n\t{advertises}\n\t{subscribes}\n}}\n'
        # SIGNATURE.
        self.signature = f"""node{self.abstract(tag=self.rosname)}"""
        return declaration

    @classmethod
    def observable_determinism(cls, assumptions=None):
        if cls.NODES is {}: raise svException("No nodes found, can not process handling of topic behaviour.")
        # PUBLIC STATE
        public_state_equivalence = f"""// Public-State Equivalence:\nfact public_state_equivalence {{\n\tno inbox"""
        states = list(filter(lambda state: not state.private and state not in svState.ASSUMPTIONS), svState.STATES.values())
        initial_assumptions = ''
        if assumptions:
            initial_assumptions = 'fact initial_assumptions {\n\t' + '\n\t'.join([re.sub(r'[ ]+',' ',p.__alloy__()) for p in assumptions]) + '\n}\n'
        for state in states:
            public_state_equivalence += f"""\n\tT1.{state.name.lower()} = T2.{state.name.lower()}"""
        public_state_equivalence += f"""\n}}\n"""
        # PUBLIC EVENT
        public = list(filter(lambda node: node.secure == False, cls.NODES.values()))
        public_event_synchronization = f"""// Public-Event Synchronization:\nfact public_event_synchronization {{"""
        for unsecured in public:
            if unsecured.advertise:
                for adv in unsecured.advertise:
                    public_event_synchronization += f"""\n\talways (all m : Message | publish[T1, {adv.signature}, m] iff publish[T2, {adv.signature}, m])"""
        for sync in cls.PUBSYNC:
            public_event_synchronization += sync
        public_event_synchronization += f"""\n}}\n"""
        return initial_assumptions + public_state_equivalence + public_event_synchronization + '\n\n'.join(list(cls.OBSERVATIONS))

    @property
    def predicate(self):
        return self._predicate

    @predicate.setter
    def predicate(self, predicate):
        from svLanguage import svPredicate
        if predicate is None: self._predicate = None
        elif not isinstance(predicate, svPredicate): raise svException("Not a predicate!")
        else: self._predicate = predicate

    @property
    def non_accessable(self):
        node_access = list(self.advertise) if self.advertise is not None else []
        node_access += list(self.subscribe) if self.subscribe is not None else []
        return list(filter(lambda t: t not in node_access, Topic.TOPICS.values()))

    # Retrieve JSON-based dict information
    @staticmethod
    def to_json(node: str):
        node   = svROSNode.NODES[node]
        subscribe, advertise = [], []
        if node.subscribe:
            subscribe = list(map(lambda subs: subs.rosname(node=node), node.subscribe))
        if node.advertise:
            advertise = list(map(lambda pubs: pubs.rosname(node=node), node.advertise))
        # Enclave is not needed at this point
        topics  = {'subscribe': subscribe, 'advertise': advertise}
        enclave = node.profile.enclave.name if node.profile else ''
        return {'node': node.index.replace('::', '/'), 'package': node.package if node.package else '', 'namespace': node.namespace if node.namespace else '', 'rosname': node.rosname if node.rosname else '', 'enclave': enclave, 'topics': topics}

    @classmethod
    def connections_to_json(cls):
        connections = []
        for node in cls.NODES.values():
            if node.connection:
                for con in node.connection:
                    for node_connected in node.connection[con]:
                        if not ((con, node_connected.rosname, node.rosname) in connections or (con, node.rosname, node_connected.rosname) in connections): 
                            connections.append((con, node.rosname, node_connected.rosname))
        return list(map(lambda con: {'relation': con[0], 'source': con[1], 'target': con[2]}, connections))

class svState(object):
    STATES      = {}
    ASSUMPTIONS = set()
    def __init__(self, name, isint=False, private=False):
        self.name, self.isint, self.private = name, isint, private
        self.signature = svState.signature(tag=name)
        self.values = set()
        svState.STATES[self.name] = self
    
    def __str__(self):
        if self.isint:
            _str_ = f"""\nsig {self.signature} in Int {{}}"""
        else:
            _str_  = f"""\nabstract sig {self.signature} {{}}"""
            _str_ += f"""\none sig {','.join([self.values_signature(value) for value in self.values])} extends {self.signature} {{}}"""
        return _str_

    @staticmethod
    def signature(tag): return 'Var_' + tag.capitalize()

    def values_signature(self, value): 
        return self.name.capitalize() + '_' + value.capitalize()

    @classmethod
    def init_state(cls, name):
        # Grammar to parse states.
        grammar = """
            sentence: one | two | three | four
            one: NAME
            two: INT NAME
            three: PUB NAME
            four: PUB INT NAME
            INT:"int"
            PUB:"public"
            NAME:/(?!\s)[a-zA-Z0-9_\/\-.\:]+/
            %import common.WS
            %ignore WS
        """
        parser = Lark(grammar, start='sentence', ambiguity='explicit')
        if not parser.parse(str(name)): raise svException(f'Failed to parse state {str(name)}.')
        t      = parser.parse(name)
        if t.children[0].data == "one":   isint, private = False, True
        if t.children[0].data == "two":   isint, private = True, True
        if t.children[0].data == "three": isint, private = False, False
        if t.children[0].data == "four":  isint, private = True, False
        name = str(t.children[0].children[::-1][0])
        return cls(name=name, isint=isint, private=private)

""" 
    The remaining classes also help to check the SROS structure within Alloy. Some methods allow svROS to retrieve data into Alloy already-made model.
"""
"SROS2-based Enclave with associated profiles."
class svROSEnclave(object):
    ENCLAVES = {}
    """
        svROSEnclave
            \_ path
            \_ profiles
    """
    def __init__(self, path, profiles):
        self.name, self.profiles, self.signature, self.ispublic = path, {}, self.abstract(tag=path), True if path == '/public' else False
        for profile in profiles:
            p = svROSProfile.init_profile(profile, enclave=self)
            self.profiles[p.profile] = p
        svROSEnclave.ENCLAVES[self.name] = self

    def abstract(self, tag): return tag.lower().replace('/', '_')

    def __str__(self):
        profiles = None if (self.profiles == {}) else ' + '.join(list(map(lambda profile: 'profile' + self.abstract(tag=profile), self.profiles)))
        if not profiles: profiles = "no profiles"
        else:            profiles = f"profiles = {profiles}"
        return f"""one sig enclave{self.signature} extends Enclave {{}} {{{profiles}}}\n"""

    def to_json(self):
        return {'name': self.name, 'profiles': [profile.to_json() for profile in self.profiles.values()]}

"SROS2-based Profile with associated priveleges."
class svROSProfile(object):
    PROFILES = {}
    """
        SROSProfile
            \_ Associated with priveleges
            \_ Later associated with a svROSNode
    """
    def __init__(self, name, namespace, can_advertise, can_subscribe, deny_advertise, deny_subscribe, enclave):
        self.name, self.namespace, self.enclave = name, namespace, enclave
        self.privileges = dict()
        self.advertise, self.subscribe, self.deny_advertise, self.deny_subscribe = can_advertise, can_subscribe, deny_advertise, deny_subscribe
        # ERROR if this function not defined: Some profiles have no corresponding node and vice-versa!!
        self.signature, self.privileges  = self.abstract(tag=namespace + name), []
        self.profile_privileges()
        # INDEX processing.
        svROSProfile.PROFILES[self.index] = self

    @classmethod
    def init_profile(cls, profile, enclave):
        namespace, name, topics = profile.get('ns'), profile.get('node'), profile.findall('./topics')
        allow_publish   = list(filter(lambda topic: topic.get('publish').strip()=='ALLOW' , list(filter(lambda t: t.get('publish'), topics))))
        allow_subscribe = list(filter(lambda topic: topic.get('subscribe').strip()=='ALLOW' , list(filter(lambda t: t.get('subscribe'), topics))))
        deny_publish    = list(filter(lambda topic: topic.get('publish').strip()=='DENY' , list(filter(lambda t: t.get('publish'), topics))))
        deny_subscribe  = list(filter(lambda topic: topic.get('subscribe').strip()=='DENY' , list(filter(lambda t: t.get('subscribe'), topics))))
        # Process ALLOWS.
        if allow_publish: advertise = list(map(lambda pub: namespace + pub.text, allow_publish[0].findall('./topic')))
        else: advertise = None
        if allow_subscribe: subscribe = list(map(lambda sub: namespace + sub.text, allow_subscribe[0].findall('./topic')))
        else: subscribe = None
        # Process DENYS.
        if deny_publish: 
            deny_publish = list(map(lambda pub: namespace + pub.text, deny_publish[0].findall('./topic')))
            for deny in deny_publish:
                if deny in advertise: raise svException(f'Failed to load profile since privilege {deny} is either defiend as ALLOW and DENY.')
        if deny_subscribe: 
            deny_subscribe = list(map(lambda sub: namespace + sub.text, deny_subscribe[0].findall('./topic')))
            for deny in deny_subscribe:
                if deny in subscribe: raise svException(f'Failed to load profile since privilege {deny} is either defiend as ALLOW and DENY.')
        # Return instance created.
        return cls(name=name, namespace=namespace, can_advertise=advertise, can_subscribe=subscribe, deny_advertise=deny_publish, deny_subscribe=deny_subscribe, enclave=enclave)

    @property
    def profile(self):
        return self.namespace + self.name
    
    @property
    def index(self):
        return self.enclave.name + self.profile

    @property
    def node(self):
        return self._node

    @node.setter
    def node(self, value):
        self._node = value
    
    # ASIDE FROM NODE DEFINITION
    def profile_privileges(self):
        rosname = self.signature
        # Process topic privilege
        if self.advertise:
            for adv in self.advertise:
                privilege = svROSPrivilege.init_privilege(node=rosname, role='advertise', rosname=adv, method='privilege')
                self.privileges.append(privilege)
        if self.subscribe:
            for sub in self.subscribe: 
                privilege = svROSPrivilege.init_privilege(node=rosname, role='subscribe', rosname=sub, method='privilege')
                self.privileges.append(privilege)
        if self.deny_advertise:
            for deny in self.deny_advertise:
                privilege = svROSPrivilege.init_privilege(node=rosname, role='advertise', rosname=deny, method='deny')
                self.privileges.append(privilege)
        if self.deny_subscribe:
            for deny in self.deny_subscribe:
                privilege = svROSPrivilege.init_privilege(node=rosname, role='subscribe', rosname=deny, method='deny')
                self.privileges.append(privilege)
        
    def abstract(self, tag): return tag.lower().replace('/', '_')

    def profile_declaration(self):
        privileges = None if (self.privileges == []) else ' + '.join(list(map(lambda p: p.signature, self.privileges)))
        if not privileges: privileges = "no privileges"
        else:              privileges = f"privileges = {privileges}"
        return f"""one sig profile{self.signature} extends Profile {{}} {{{privileges}}}\n"""

    def privilege_declaration(self):
        _str_return_ = ""
        for privilege in self.privileges: _str_return_ += str(privilege)
        return _str_return_

    def __str__(self):
        return self.profile_declaration() + self.privilege_declaration()

    def to_json(self):
        advertise, subscribe = [], []
        if self.advertise: advertise = self.advertise
        if self.subscribe: subscribe = self.subscribe
        return {'name': self.name if self.name else '', 'namespace': self.namespace if self.namespace else '', 'advertise': advertise, 'deny_advertise': self.deny_advertise, 'subscribe': subscribe, 'deny_subscribe': self.deny_subscribe}

class svROSObject(object):
    OBJECTS = {}
    def __init__(self, name):
        self.name = 'object' + name if name.startswith('_') else '_' + name
        svROSObject.OBJECTS[name] = self

    @classmethod
    def init_object(cls, name):
        if name in cls.OBJECTS: return cls.OBJECTS[name]
        return cls(name=name)
    
    def __str__(self):
        return f'one sig {self.name} extends Object {{}}\n'

class svROSPrivilege(object):
    PRIVILEGES       = {'Advertise', 'Subscribe'}
    METHODS          = {'Privilege', 'Deny'}
    PRIVILEGES_SET   = {}
    def __init__(self, index, signature, role, rosname, rule):
        self.signature       = self.abstract(tag=signature)
        self.role, self.rule, self.object = role.capitalize(), rule.capitalize(), svROSObject.init_object(name=self.abstract(tag=rosname))
        if not self.role in svROSPrivilege.PRIVILEGES: raise svException('Not identified role.')
        svROSPrivilege.PRIVILEGES_SET[index] = self

    @classmethod
    def init_privilege(cls, node, role, rosname, method):
        if not role.capitalize() in svROSPrivilege.PRIVILEGES: raise svException('Not identified role.')
        if not method.capitalize() in svROSPrivilege.METHODS:  raise svException('Not identified method.')
        if method.capitalize().strip() == 'Deny': rule = 'Deny' 
        else: rule = 'Allow'
        # INDEX PROCESSING.
        index = node + rosname + '_' + rule.lower()
        index = index if not index.startswith('_') else index[1:]
        if index in cls.PRIVILEGES_SET:
            return cls.PRIVILEGES_SET[index]
        else:
            return cls(index=index, signature=f'{index}', role=role, rosname=rosname, rule=rule)

    def abstract(self, tag): return tag.lower().replace('/', '_')

    def __str__(self):
        _str_ = f"""one sig {self.signature} extends Privilege {{}} {{role = {self.role}\nrule = {self.rule}\nobject = {self.object.name}}}\n""" 
        return _str_

###############################
# === ANALYSING !! YAY :))) ===
###############################
class svExecution(object):
    """
        Execution Traces => SELF-COMPOSITION
    """
    def __init__(self, name, signature):
        self.name, self.signature = name, signature

    @classmethod
    def create_executions(cls):
        t1 = cls(name='Trace_1', signature='T1')
        t2 = cls(name='Trace_2', signature='T2')
        # Convert TO ALLOY. 
        _str_  = f"""abstract sig Trace {{\n\tvar inbox: Topic -> (seq Message)"""
        states, only_one_per_state = '', []
        nop = set()
        # Predicate SYSTEM
        system_str   = f"""pred system [t : Trace] {{"""
        for state in svState.STATES:
            state = svState.STATES[state]
            states += str(state)
            _str_ += f""",\n\tvar {state.name.lower()}: one {state.signature}"""
            # if state.private:
            #     _str_ += f""",\n\tvar {state.name.lower()}: one {state.signature}"""
            # if not state.private: 
            #     # Execution signature:
            #     _str_ += f""",\n\tvar {state.name.lower()}: {state.signature} lone -> (0 + 1)"""
            #     only_one_per_state.append(f"""one {state.name.lower()}""")
            #     # Aside from Execution Signature:
            #     system_str   += f"""\n\tall s : {state.signature} | t.{state.name.lower()} = s->1 implies t.{state.name.lower()}' = s->0"""
            #     # Public state equivalence?
            #     public_state += f"""\n\tExecution.{state.name.lower()}) = {state.default}->0\n\talways (some T1.{state.name.lower()}.1 iff some T2.{state.name.lower()}.1)"""
            nop.add(state.name.lower())
        _str_ += f"""\n}}""" # {{ {' and '.join(only_one_per_state)} }} \n"""
        # Predicate NOP
        nop_str = f"""pred nop [t : Trace] {{\n\tt.inbox' = t.inbox"""
        for n in nop:
            nop_str    += f"""\n\tt.{n}' = t.{n}"""
        nop_str += f"""\n}}\n"""
        # svPredicate
        from svLanguage import svPredicate
        system_str += f"""\n\t// System trace executions.\n\t{'[t] or '.join(list(filter(lambda not_sub: not not_sub.is_sub_predicate, svPredicate.NODE_BEHAVIOURS.keys())))}[t]\n}}"""
        _str_ += f""" one sig {t1.signature}, {t2.signature} extends Trace {{}}\n"""
        return '/* === STATES === */' + states + '\n/* === STATES === */\n\n/* === SELF-COMPOSITION === */\n' + _str_ + '/* === SELF-COMPOSITION === */\n\n' + nop_str + system_str # '\n// Public-State Equivalence and Synchronization:\n' + public_state 