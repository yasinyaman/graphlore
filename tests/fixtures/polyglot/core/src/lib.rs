pub enum Mode {
    Fast,
    Safe,
}

pub trait Pool {
    fn acquire(&self) -> i32;
}

pub struct Fixed;

impl Pool for Fixed {
    fn acquire(&self) -> i32 {
        1
    }
}

pub fn run(pool: &dyn Pool) -> i32 {
    pool.acquire()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn runs() {
        let n = run(&Fixed);
        assert_eq!(n, 1);
    }
}
