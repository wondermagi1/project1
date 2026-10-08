// 可选的浏览器脚本回归测试：node tests/test_webui.cjs，无 npm 依赖。
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '../agent/static/workbench.js'), 'utf8');
new vm.Script(source); // 整段页面脚本必须能够解析
const start = source.indexOf('    const escapeHtml');
const end = source.indexOf('    function scrollDown');
const context = vm.createContext({});
vm.runInContext(source.slice(start, end), context);
for (const input of ['| ordinary text', '| a | b |', '### heading', '```python\nprint(1)\n```', '中文段落']) {
  context.input = input;
  const result = vm.runInContext('renderMarkdown(input)', context, {timeout: 1000});
  assert.ok(result.length > 0);
}
context.input = '```" onmouseover="alert(1)\n<img src=x onerror=alert(1)>\n```';
const output = vm.runInContext('renderMarkdown(input)', context, {timeout: 1000});
assert.ok(!output.includes('data-lang="" onmouseover='));
assert.ok(output.includes('&quot;'));
assert.ok(!output.includes('<img'));
console.log('Web UI regression checks passed');
