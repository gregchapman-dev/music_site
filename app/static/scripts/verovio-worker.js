// ---
// layout: empty
// permalink: /static/scripts/verovio-worker.js
// vim: ts=3
// ---

// {% comment %}
//
// Web worker interface for verovio, which separates notation rendering
// into a separate thread from the user interface.
//
// For more information about web workers:
//      https://developer.mozilla.org/en-US/docs/Web/API/Web_Workers_API/Using_web_workers
//
// {% endcomment %}

self.methods = null;


/////////////////////////////
//
// WASM installation variable:
//

importScripts('/static/scripts/verovio-toolkit-wasm.js');
// importScripts("/static/scripts/humdrumValidator.js");
importScripts("/static/scripts/verovio-calls.js");


// New method for loading verovio:
verovio.module.onRuntimeInitialized = () => {
	methods = new verovioCalls();
	methods.vrvToolkit = new verovio.toolkit();
	console.log(`Verovio (WASM) ${methods.vrvToolkit.getVersion()} loaded`);
	postMessage({method: "ready"});
};

// Old method for loading verovio:
// self.Module = {
// 	onRuntimeInitialized: function() {
// 			methods = new verovioCalls();
// 			methods.vrvToolkit = new verovio.toolkit();
// 			console.log(`Verovio (WASM) ${methods.vrvToolkit.getVersion()} loaded`);
// 			postMessage({method: "ready"});
// 	}
// };

//
// WASM
//
//////////////////////////////



// force local:
//importScripts("/scripts/verovio-toolkit.js");
//importScripts("/scripts/humdrumValidator.js");
//importScripts("/scripts/verovio-calls.js");


//////////////////////////////
//
// resolve --
//

function resolve(data, result) {
	postMessage({
		method: data.method,
		idx: data.idx,
		result: result,
		success: true
	});
};



//////////////////////////////
//
// reject --
//

function reject(data, result) {
	postMessage({
		method: data.method,
		idx: data.idx,
		result: result,
		success: false
	});
};


//////////////////////////////
//
// message event listener --
//

addEventListener("message", function(oEvent) {
	try {
		resolve(oEvent.data, methods[oEvent.data.method].apply(methods, oEvent.data.args));
	} catch(err) {
		reject(oEvent.data, err);
	};
});


// non-wasm:
// methods = new verovioCalls();
// methods.vrvToolkit = new verovio.toolkit();
// postMessage({method: "ready"});


