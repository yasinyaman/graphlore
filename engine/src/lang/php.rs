//! PHP. Namespaces (`namespace Shop;` and the block form) only prefix names.
//! Closures are not definitions.

use super::LanguageModule;

pub struct Php;

impl LanguageModule for Php {}
