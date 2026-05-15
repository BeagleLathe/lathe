; TypeScript tags query.

(function_declaration
  name: (identifier) @name) @function

(method_definition
  name: (property_identifier) @name) @method

(class_declaration
  name: (type_identifier) @name) @class

(interface_declaration
  name: (type_identifier) @name) @interface

(type_alias_declaration
  name: (type_identifier) @name) @type

(enum_declaration
  name: (identifier) @name) @enum

(import_statement) @import

; const/let/var with simple identifier destructuring at module scope.
; Lexical bindings show up as `lexical_declaration` (let/const) or
; `variable_declaration` (var); the engine only treats the @function
; capture as truncatable, so this is symbol-list information only.
(lexical_declaration
  (variable_declarator
    name: (identifier) @name
    value: [(arrow_function) (function_expression)] @function))

(variable_declaration
  (variable_declarator
    name: (identifier) @name
    value: [(arrow_function) (function_expression)] @function))
