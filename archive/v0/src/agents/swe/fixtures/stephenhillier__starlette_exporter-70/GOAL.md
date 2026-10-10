Feature request: Support async label callback
Would be nice if we could define async callback functions that will produce metric label. If request body is needed to be parsed in the callback, we need to await since starlette body() is a coroutine. I inspected the code and it seems trivial to add. Thanks!
