export async function controlledContext(browser, options = {}) {
  const context = await browser.newContext({...options,serviceWorkers:"block"});
  const policy = {blockedRequests:0};
  await context.route("**/*",async(route)=>{policy.blockedRequests+=1;await route.abort("blockedbyclient");});
  await context.routeWebSocket("**/*",(socket)=>{policy.blockedRequests+=1;socket.close({code:1008,reason:"offline diagram renderer"});});
  return {context,policy};
}
