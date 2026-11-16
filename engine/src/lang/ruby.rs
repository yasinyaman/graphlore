//! Ruby. `module` is a definition (graphify makes a node of it too), and a
//! constant assigned a block-taking call (`Line = Struct.new(:a) do … end`) is a
//! class named after the constant.

use super::LanguageModule;

pub struct Ruby;

impl LanguageModule for Ruby {}
