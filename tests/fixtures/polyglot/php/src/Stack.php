<?php

namespace Shop;

use Psr\Http\Message\RequestInterface;

class Stack
{
    private array $handlers = [];

    public function push(callable $middleware, string $name = ''): void
    {
        $this->handlers[] = static function (RequestInterface $request) use ($middleware) {
            return $middleware($request);
        };
    }
}
