; Python tags query.
; Captures used by the engine: @function, @class, @import, @constant, @name.

(function_definition
  name: (identifier) @name) @function

(class_definition
  name: (identifier) @name) @class

(import_statement) @import
(import_from_statement) @import

((assignment
   left: (identifier) @name)
 (#match? @name "^[A-Z][A-Z0-9_]*$")) @constant
