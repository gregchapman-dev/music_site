    let gScoreMei = '';       // for rendering with verovio

    function containsScore(mei) {
        return (mei !== undefined && mei != '')
    }

    async function processResponse(resp) {
        if (resp.ok) {
            jsonBody = await resp.json();
            showUser = jsonBody['showUser']  // a string
            // 888 figure out how to display text in template html (document.something = showUser)

            appendToConsole = jsonBody['appendToConsole']
            if (appendToConsole !== undefined) {
                console.log(appendToConsole);
            }

            gScoreMei = jsonBody['mei'];
            if (containsScore(gScoreMei)) {
                console.log("rendering score from response")
                renderMusic();
            }
        }
    }

    function renderMusic() {
        if (containsScore(gScoreMei)) {
            // write gScoreMei to #currentScore element, and pass it
            // to displayHumdrum.
            let currScoreEl = document.querySelector("#currentScore");
            if (!currScoreEl) {
                console.log("no currScore element to hold current score");
                return;
            }

            console.log("writing gScoreMei to currentScore element.textContent");
            currScoreEl.textContent = gScoreMei;
            console.log("calling displayHumdrum");
            displayHumdrum({
                source: "currentScore",
                svgTarget: "notation",
                autoResize: "true",
                header: "true",
                scale: 50,
                scaleToPageSize: true,
            });
        }
        else {
            console.error("no score to render!");
        }
    }

    function transpose() {
        formData = new FormData();
        formData.append('command', 'transpose');
        formData.append('semitones', +2);  // 2 semitones: a whole tone up
        fetch(
            '/command',
            {method: 'POST', body: formData }
        ).then( async (resp) => {
            await processResponse(resp);
        });
    }

    function undo() {
        formData = new FormData();
        formData.append('command', 'undo');
        fetch(
            '/command',
            {method: 'POST', body: formData }
        ).then( async (resp) => {
            await processResponse(resp);
        });
    }

    function redo() {
        formData = new FormData();
        formData.append('command', 'redo');
        fetch(
            '/command',
            {method: 'POST', body: formData }
        ).then( async (resp) => {
            await processResponse(resp);
        });
    }

    function shopIt(arrType) {
        formData = new FormData();
        formData.append('command', 'shopIt');
        formData.append('arrangementType', arrType);
        fetch(
            '/command',
            {method: 'POST', body: formData}
        ).then( async (resp) => {
            await processResponse(resp);
        });
    }

    function shopItUpper() {
        shopIt('UpperVoices');
    }

    function shopItLower() {
        shopIt('LowerVoices');
    }

    function hideChordOptions() {
        formData = new FormData();
        formData.append('command', 'hideChordOptions');
        fetch(
            '/command',
            {method: 'POST', body: formData}
        ).then( async (resp) => {
            await processResponse(resp);
        });
    }

    function htmlDecode(input) {
        var doc = new DOMParser().parseFromString(input, "text/html");
        return doc.documentElement.textContent;
    }

    function handleFiles() {
        // Assumes only one score file (since our HTML only allows one).
        // POSTs to server to be saved for later processing and displays
        // the resulting MEI (from response).
        const selectedFile = this.files[0];
        const reader = new FileReader();
        reader.onload = async (e) => {
            // POST file contents to '/score'
            // The response will be the MEI equivalent of the score file
            // contents you POSTed (converted by server, even if what you
            // POSTed was MEI).
            const formData = new FormData();
            formData.append('file', selectedFile);
            formData.append('filename', selectedFile.name);
            const resp = await fetch(
                '/score',
                {method: 'POST', body: formData }
            );
            await processResponse(resp);
        };
        reader.readAsBinaryString(selectedFile);
    }

    function chooseNewChordOption(target) {
        formData = new FormData();
        formData.append('command', 'chooseChordOption');
        formData.append('chordOptionId', target.id)
        fetch(
            '/command',
            {method: 'POST', body: formData }
        ).then( async (resp) => {
            await processResponse(resp);
        });
    }

    function svgClick(event) {
        let target = event.target;
        while (target) {
            if (target.nodeName === "svg") {
                break;
            }
            if (target.nodeName === "g"
                    && target.id.startsWith("dir-")) {
                chooseNewChordOption(target);
                break;
            }
            target = target.parentNode;
        }
    }

    // ----------- main code ----------------

    document.addEventListener("DOMContentLoaded", (event) => {
        initialScore = document.getElementById("initialScore");
        gScoreMei = htmlDecode(initialScore.text.trim())
        if (containsScore(gScoreMei)) {
            console.log("loaded initialScore into gScoreMei")
        }
        else {
            console.log("no initialScore")
        }

        if (containsScore(gScoreMei)) {
            console.log("attempting to render initialScore (in DOMContentLoaded)")
            renderMusic()
        }
        else {
            console.log("no initialScore to render (in DOMContentLoaded)")
        }
    });

    const notationSvg = document.querySelector('#notation');
    const transposeBtn = document.querySelector('#transpose');
    const shopItUpperBtn = document.querySelector('#shopItUpper');
    const shopItLowerBtn = document.querySelector('#shopItLower');
    const hideChordOptionsBtn = document.querySelector('#hideChordOptions');
    const undoBtn = document.querySelector('#undo')
    const redoBtn = document.querySelector('#redo')
    const downloadMusicXMLTag = document.querySelector('#downloadAnchorMusicXML');
    const downloadHumdrumTag = document.querySelector('#downloadAnchorHumdrum');
    const downloadMEITag = document.querySelector('#downloadAnchorMEI');
    const fileElem = document.querySelector("#fileElem");
    fileElem.addEventListener("change", handleFiles, false);
    const fileSelect = document.querySelector("#fileSelect");
    fileSelect.addEventListener(
        "click",
        (e) => {
            if (fileElem) {
                fileElem.click();
            }
        },
        false,
    );

    notationSvg.addEventListener("click", svgClick);
    transposeBtn.addEventListener("click", transpose);
    shopItUpperBtn.addEventListener("click", shopItUpper);
    shopItLowerBtn.addEventListener("click", shopItLower);
    hideChordOptionsBtn.addEventListener("click", hideChordOptions);
    undoBtn.addEventListener("click", undo);
    redoBtn.addEventListener("click", redo);
