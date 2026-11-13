const assert=require('node:assert/strict'),{lint}=require('../tools/shader_lint.cjs');
assert(lint('float b = mod(floor(mask / pow(2.0, n)), 2.0);').length);
assert(lint('float v = pow(-x, 0.5);').length);
assert(lint('float v = pow(sin(x), 0.5);').length);
assert.equal(lint('float v = pow(max(x, 0.0), 0.5); int b = (mask >> n) & 1;').length,0);
console.log('Shader portability negative tests OK');
