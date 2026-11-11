// Development dependencies never belong in the installable plugin root.
const {createRequire}=require('node:module');
const path=require('node:path');
module.exports=createRequire(path.join(__dirname,'../dev/package.json'));
