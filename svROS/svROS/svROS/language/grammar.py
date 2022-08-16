# Copyright © 2022 Luís Ribeiro
# Grammar for svLanguage.

from tools.InfoHandler import svException, svWarning
GRAMMAR = f"""

    axiom : REQUIRES_TOKEN condition 
          | READS_TOKEN reads 
          | PUBLISHES_TOKEN publishes 
          | ALTERS_TOKEN alters

    condition  : [condition AND_OPERATOR] cond
    cond       : ( NO_OPERATOR | SOME_OPERATOR ) ( TOPIC | PREDICATE | evaluation )
    evaluation : ( TOPIC | STATE ) EQUAL_OPERATOR VALUE

    reads       : reads_only 
                | reads_perf
    reads_only  : TOPIC
    reads_perf  : TOPIC "then" "{{" disjunction "}}"

    disjunction : [disjunction OR_OPERATOR] conjunction
    conjunction : [conjunction AND_OPERATOR] read_condition

    read_condition   : ["m" ( EQUAL_OPERATOR | GREATER_OPERATOR | LESSER_OPERATOR ) VALUE CONSEQUENCE_OPERATOR] read_consequence
    read_consequence : ( NO_OPERATOR ) PREDICATE
                     | ( TOPIC | STATE ) ( EQUAL_OPERATOR | INC_OPERATOR | DEC_OPERATOR ) VALUE
    
    publishes : TOPIC [ EQUAL_OPERATOR VALUE ]

    alters : [alters AND_OPERATOR] alters_condition
    alters_condition : STATE ( EQUAL_OPERATOR | INC_OPERATOR | DEC_OPERATOR ) VALUE

    REQUIRES_TOKEN  : "requires"
    READS_TOKEN     : "reads"
    PUBLISHES_TOKEN : "publishes"
    ALTERS_TOKEN    : "alters"

    NO_OPERATOR   : "no" | "not"
    SOME_OPERATOR : "some" | "exists"

    OR_OPERATOR : "or"  | "++"
    AND_OPERATOR  : "and" | "&&"
    CONSEQUENCE_OPERATOR : "implies" | "=>"
    
    EQUAL_OPERATOR   : "eql" | "="
    GREATER_OPERATOR : "gtr" | ">"
    LESSER_OPERATOR  : "les" | "<"

    INC_OPERATOR   : "add" | "+="
    DEC_OPERATOR   : "rmv" | "-="

    VALUE     : /(?!=>)[a-zA-Z0-9_\/\-.\:]+/
    TOPIC     : /(?!\s)[a-zA-Z0-9_\/\-.\:]+/
    STATE     : /(?!\s)\$[a-zA-Z0-9_\/\-.\:]+/
    PREDICATE : /(?!\s)\?[a-zA-Z0-9_\/\-.\:]+/

    %import common.WS
    %ignore WS
"""

from lark import Lark, tree, Token, Transformer
from lark.exceptions import UnexpectedCharacters, UnexpectedToken
from svData import Topic, svState
class GrammarParser(object):
    """
        Grammar Main Parser
    """
    GRAMMAR = f'{GRAMMAR}'

    @classmethod
    def parse(cls, text=''):
        if text == '': return
        grammar, parser = cls.GRAMMAR, Lark(cls.GRAMMAR, parser="lalr", start='axiom', transformer=LanguageTransformer())
        # PARSE!
        try:
            conditions = parser.parse(text)
        except (UnexpectedToken, UnexpectedCharacters, SyntaxError) as e:
            raise svException(f'Failed to parse property {text}: {e}')
        try:
            return conditions
        except AttributeError: raise svException(f'Failed to parse property {text}: {e}')

ALLOY_OPERATORS = {
    "NO_OPERATOR": "no",
    "SOME_OPERATOR": "some",
    "OR_OPERATOR": "or",
    "AND_OPERATOR": "and",
    "CONSEQUENCE_OPERATOR": "implies",
    "EQUAL_OPERATOR": "=",
    "GREATER_OPERATOR": "gt",
    "LESSER_OPERATOR": "le",
    "INC_OPERATOR": "plus",
    "DEC_OPERATOR": "minus"
}
        
###############################
# === LANGUAGE TRANSFORMER  ===
###############################
class BinaryOperator(object):
    # OPERATORS
    OPERATORS = {"OR_OPERATOR", "AND_OPERATOR", "EQUAL_OPERATOR", "GREATER_OPERATOR", "LESSER_OPERATOR", "INC_OPERATOR", "DEC_OPERATOR"}

    def __init__(self, op, argument, value):
        assert op in self.OPERATORS
        self.operator, self.argument1, self.argument2 = op, argument, value

    def children(self):
        return (self.operand1, self.operand2)

class LanguageTransformer(Transformer):

    def __init__(self):
        self.OBJECTS = set()

    def axiom(self, children):
        if self.OBJECTS.__len__() == 1: return next(iter(self.OBJECTS))
        else: return MultipleConditions(conditions=list(self.OBJECTS))

    # CONDITIONAL
    def evaluation_cond(self, children):
        if not children.__len__() == 3: raise svException("")
        predicate, entity, value = children[0].type, children[0].value, children[2].value
        return predicate, entity, value

    def cond(self, children):
        if not children.__len__() == 2: raise svException("")
        quantifier, predicate = children[0].type, children[1]
        if isinstance(predicate, Token):
            predicate, entity, value = children[1].type, children[1].value, None
        else:
            predicate, entity, value = LanguageTransformer.evaluation_cond(self, children[1].children)
        conditional = Conditional(quantifier=quantifier, type=predicate, entity=entity, value=value)
        self.OBJECTS.add(conditional)
        return conditional

    # READS
    def reads_only(self, children):
        read = Read(entity=children[0].value, conditions=None, readonly=True)
        self.OBJECTS.add(read)
        return read

    def reads_perf(self, children):
        topic, conditions = children[0].value, self.conj
        read = Read(entity=topic, conditions=conditions, readonly=False)
        self.OBJECTS.add(read)
        return read

    def conjunction(self, children):
        if children[1] is None: 
            try: self.conj.append([children[2]])
            except AttributeError: self.conj = [[children[2]]]
        else: 
            try: index = self.conj.__len__() - 1
            except AttributeError: index = 0
            self.conj[index].append(children[2])

    def read_consequence(self, children):
        if children.__len__() > 2:
            entity, type, relation, value = children[0].value, children[0].type, children[1].type, children[2].value
            return ReadConsequence(entity=entity, type=type, relation=relation, value=value)
        else:
            entity, type = children[1].value, children[0].type
            return ReadConsequence(entity=entity, type=type, relation=None, value=None, predicate=True)

    def read_condition(self, children):
        try: self.disj.append(children)
        except AttributeError: self.disj = [children]
        # no conditional!
        if children[0] is None: return children[3]
        else:
            conditional, value = children[0].type, children[1].value
            return ReadConditional(conditional=conditional, value=value, consequence=children[3])

    # PUBLISH
    def publishes(self, children):
        if children.__len__() > 3: raise svException("")
        entity = children[0].value
        if children.__len__() > 1: value = children[::-1][0]
        else: value = None
        publish = Publish(entity=entity, value=value)
        self.OBJECTS.add(publish)
        return publish

    # ALTERS
    def alters_condition(self, children):
        if not children.__len__() == 3: raise svException("")
        entity, relation, value = (children[0].value)[1:], children[1].type, children[2].value
        alter = Alter(entity=entity, relation=relation, value=value)
        self.OBJECTS.add(alter)
        return alter

###############################
# === PARSER FROM TRANSFMER ===
###############################
class MultipleConditions(object):

    def __init__(self, conditions):
        self.conditions = conditions
    
    def __alloy__(self):
        return ' and '.join([f'({cond.__alloy__()})' for cond in self.conditions])

class Conditional(object):

    def __init__(self, quantifier, type, entity, value=None):
        self.quantifier, self.type, self.value = quantifier, type, value
        if self.type in {'PREDICATE', 'STATE'}: self.entity = entity[1:]
        else: self.entity = entity
        if self.type == 'TOPIC':
            if entity not in Topic.TOPICS: raise svException(f"Channel {entity} does not exist!")

    def __alloy__(self):
        alloy = f'{ALLOY_OPERATORS[str(self.quantifier)]} '
        entity = str(self.entity).lower().replace('/','_')
        if self.type == 'PREDICATE':
            if self.quantifier == 'NO_OPERATOR':
                alloy   += f'{entity}[t]'
            else: alloy  = f'{entity}[t]'
        else: 
            channel = Topic.TOPICS[self.entity]
            alloy += f't.inbox[{channel.signature}]'
        return alloy

class ReadConsequence(object):

    def __init__(self, entity, type, relation, value, predicate=False):
        if not predicate:
            self.entity, self.type, self.relation, self.value, self.replicate = entity, type, relation, value, False
            match type:
                case 'STATE':
                    if entity[1:] not in svState.STATES: raise svException(f"State {entity} does not exist!")
                case 'TOPIC':
                    if entity not in Topic.TOPICS: raise svException(f"Channel {entity} does not exist!")
            if self.type in {'PREDICATE', 'STATE'}: self.entity = entity[1:]
            else: self.entity = entity
            # value can be m
            if value == 'm': self.replicate = True
        else:
            self.entity, self.type, self.relation, self.value, self.predicate = entity, type, None, None, True 

    def __alloy__(self, channel):
        if self.type is None:
            assert(self.predicate)
            if self.type:
                return f'{ALLOY_OPERATORS[self.type]} {self.entity}[t]'
            return f'{self.entity}[t]'
        if self.type == 'TOPIC':
            conseq_channel = Topic.TOPICS[self.entity]
            if self.relation in {'EQUAL_OPERATOR', 'INC_OPERATOR'}:
                if not self.replicate:
                    if self.value not in conseq_channel.message_type.value.values: 
                        raise svException(f"Channel {conseq_channel.signature} value {self.value} does not exist!")
                else:
                    if not bool(channel.message_type == conseq_channel.message_type):
                        raise svException(f"Channel {conseq_channel.signature} can not replicate value of {channel.signature}, as they have different message types.")
                return f"t.inbox'[{conseq_channel.signature}] = add[t.inbox[{conseq_channel.signature}], {self.value}]"
        # STATE
        return Alter(entity=self.entity, relation=self.relation, value=self.value).__alloy__()

class ReadConditional(object):

    def __init__(self, conditional, value, consequence=None):
        self.conditional, self.value, self.consequence = conditional, value, consequence

    def __alloy__(self, channel):
        if self.consequence is None: return ''
        if self.value not in channel.message_type.value.values: raise svException(f"Channel {channel.signature} value {self.value} does not exist!")
        value = self.value
        if self.conditional == 'EQUAL_OPERATOR': 
            return f"m = {value} implies {self.consequence.__alloy__(channel=channel)}"
        return f"{ALLOY_OPERATORS[self.conditional]}[m, {value}] implies {self.consequence.__alloy__(channel=channel)}"

class Read(object):

    def __init__(self, entity, conditions, readonly=False):
        if entity not in Topic.TOPICS: raise svException(f"Channel {entity} does not exist!")
        self.entity, self.conditions, self.readonly = entity, conditions, readonly

    def __conjunction__(self, conditional, channel):
        return ' and '.join([f'({cond.__alloy__(channel=channel)})' for cond in conditional])

    def __alloy__(self):
        channel = Topic.TOPICS[self.entity]
        return f"let m = first[t.inbox[{channel.signature}]] {{\n\t\t" + " or ".join([f'({self.__conjunction__(conditional=c, channel=channel)})' for c in self.conditions]) + f"\n\t}}" + f"\n\tt.inbox'[{channel.signature}] = rest[t.inbox[{channel.signature}]] "

class Publish(object):

    def __init__(self, entity, value=None):
        if entity not in Topic.TOPICS: raise svException(f"Channel {entity} does not exist!")
        self.entity, self.value = entity, value

    def __alloy__(self):
        channel = Topic.TOPICS[self.entity]
        if self.value is None:
            return f"some m : Message | t.inbox'[{channel.signature}] = add[t.inbox[{channel.signature}], m]"
        return f"some m : Message | m = {self.value} implies t.inbox'[{channel.signature}] = add[t.inbox[{channel.signature}], m]"

class Alter(object):

    def __init__(self, entity, relation, value):
        if entity not in svState.STATES: raise svException(f"State {entity} does not exist!")
        self.entity, self.relation, self.value = entity, relation, value

    def __alloy__(self):
        state = svState.STATES[self.entity]
        if self.value not in state.values: raise svException(f"State value {self.value} does not exist!")
        value = state.values_signature(value=self.value)
        if self.relation == 'EQUAL_OPERATOR': 
            return f"t.{state.name.lower()}' = {value}->1"
        return f"t.{state.name.lower()}' = {ALLOY_OPERATORS[self.relation]}[t.{state.name.lower()}, {value}]"