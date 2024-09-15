    let gScore;
//     let gScoreMusicXml;  // for downloading
//     let gScoreHumdrum;   // for uploading with commands (it's way smaller)
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
            let tk = assureVerovioInitialized()
            let svg = tk.renderData(gScoreMei, {});
            document.getElementById("notation").innerHTML = svg;
        }
        else {
            console.error("no score to render!")
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

    function shopIt(arrType) {
        formData = new FormData();
        formData.append('command', 'shopIt');
        formData.append('arrangementType', arrType);
        fetch(
            '/command',
            {method: 'POST', body: formData }
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

    function htmlDecode(input) {
        var doc = new DOMParser().parseFromString(input, "text/html");
        return doc.documentElement.textContent;
    }

    function handleFiles() {
        // Assumes only one MusicXML file (since our HTML only allows one).
        // POSTs to server to be saved for later processing; displays the
        // resulting MEI (from response), remembers the Humdrum (for quick
        // use during commands) and adds a download data URL to the DOM
        // for the user to save off the MusicXML).
        const selectedFile = this.files[0];
        const reader = new FileReader();
        reader.onload = async (e) => {
            // POST file contents to '/score'
            // The response will be the MusicXML equivalent of the score file
            // contents you POSTed (converted by server, even if what you
            // POSTed was MusicXML).
            const formData = new FormData();
            // formData.append('score', reader.result, selectedFile.name)
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

    let verovioToolkit;

    function assureVerovioInitialized() {
        if (verovioToolkit) {
            return verovioToolkit
        }
        verovioToolkit = new verovio.toolkit();
        console.log("Verovio has loaded!");
//             console.log("Verovio default options:", verovioToolkit.getDefaultOptions());
        verovioToolkit.setOptions({
//                 breaks: "none",
            scale: 30,
//                 landscape: true,
            scaleToPageSize: true,
            pageWidth: 1000,
//                 adjustPageWidth: true,
//                 adjustPageHeight: true
        });
//             console.log("Verovio options:", verovioToolkit.getOptions());
        return verovioToolkit
    }

    // ----------- main code ----------------

    document.addEventListener("DOMContentLoaded", (event) => {
        initialScore = document.getElementById("initialScore");
        gScoreMei = htmlDecode(initialScore.text.trim())
        if (containsScore(gScoreMei)) {
            console.log("rendering initialScore")
            renderMusic()
        }
        else {
            console.log("no initialScore to render")
        }
        verovio.module.onRuntimeInitialized = () => {
            assureVerovioInitialized()
            if (containsScore(gScoreMei)) {
                console.log("re-rendering initialScore")
                renderMusic()
            }
            else {
                console.log("no initialScore to re-render")
            }
        }
    });

    const notationSvg = document.querySelector('#notation');
    const transposeBtn = document.querySelector('#transpose');
    const shopItUpperBtn = document.querySelector('#shopItUpper');
    const shopItLowerBtn = document.querySelector('#shopItLower');
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
