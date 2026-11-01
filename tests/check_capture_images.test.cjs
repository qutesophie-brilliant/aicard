const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const code = fs.readFileSync(path.join(__dirname, '../app/static/check-capture.js'), 'utf8');
const start = code.indexOf('  function captureImageSource(n) {');
const end = code.indexOf('  const out =', start);
assert(start >= 0 && end > start);
const read = vm.runInNewContext(code.slice(start, end) + '\ncaptureImageSource;');
const source = attrs => ({getAttribute:k => attrs[k] || null});
const image = (attrs, sources=[]) => ({...source(attrs), closest:() => sources.length ?
  {querySelectorAll:() => sources.map(source)} : null});
assert.equal(read(image({'src':'data:image/png;base64,x','data-src':'/detail.jpg'})), '/detail.jpg');
assert.equal(read(image({'src':'/thumb.jpg','srcset':'/small.jpg 400w, /large.jpg 1600w'})), '/large.jpg');
assert.equal(read(image({'src':'data:image/png;base64,x'}, [
  {media:'(max-width:600px)',srcset:'/mobile.jpg 600w'},
  {srcset:'/detail.jpg?fit=stretch,1 1600w'}])), '/detail.jpg?fit=stretch,1');
assert.equal(read(image({'data-src':'data:image/png;base64,x','src':'/visible.jpg'})), '/visible.jpg');
assert.equal(read(image({'srcset':'/one.jpg 1x,/two.jpg 2x'})), '/two.jpg');
assert.equal(read(image({})), '');
assert.equal(read(image({'src':'data:image/png;base64,x'})), '');
console.log('7 image-source cases passed');
