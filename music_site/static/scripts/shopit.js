    let gScore;
//     let gScoreMusicXml;  // for downloading
//     let gScoreHumdrum;   // for uploading with commands (it's way smaller)
    let gScoreMei = '';       // for rendering with verovio

    async function processMusicFromResponse(resp) {
        if (resp.ok) {
            jsonBody = await resp.json();
            gScoreMei = jsonBody['mei'];
            renderMusic();
            await replaceDataUrl(downloadMEITag, gScoreMei, 'Score.mei');
        }
    }

    function renderMusic() {
        if (gScoreMei != '') {
            let svg = tk.renderData(gScoreMei, {});
            document.getElementById("notation").innerHTML = svg;
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
            await processMusicFromResponse(resp);
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
            await processMusicFromResponse(resp);
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

    async function bytesToBase64DataUrl(bytes, type = "application/octet-stream") {
        return await new Promise((resolve, reject) => {
            const reader = Object.assign(new FileReader(), {
                onload: () => resolve(reader.result),
                onerror: () => reject(reader.error),
            });
            reader.readAsDataURL(new File([bytes], "", { type }));
        });
    }

    async function dataUrlToBytes(dataUrl) {
        const res = await fetch(dataUrl);
        if (!res.ok) {
            throw new Error(`Response status: ${res.status}`);
        }
        return new Uint8Array(await res.arrayBuffer());
    }

    async function replaceDataUrl(anchorTag, text, name) {
        anchorTag.download = name;
        anchorTag.href = await bytesToBase64DataUrl(text);
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
            await processMusicFromResponse(resp);
        };
        reader.readAsBinaryString(selectedFile);
    }

    function chooseNewChordOption(target) {
        formData = new FormData();
        formData.append('command', 'chooseChordOption');
        formData.append('score', gScoreMei);
        formData.append('format', 'mei');
        formData.append('chordOptionId', target.id)
        fetch(
            '/command',
            {method: 'POST', body: formData }
        ).then( async (resp) => {
            await processMusicFromResponse(resp);
        });
    }

    function svgClick(event) {
        let target = event.target;
        while (target) {
            if (target.nodeName === "svg") {
                break;
            }
            if (target.nodeName === "g"
                    && target.id.startsWith("dir-")) {  // ultimately "harm-", but "dir-" for now
                if (target.id.endsWith("_")) {  // "_" means already selected
                    break;
                }
                chooseNewChordOption(target);
                break;
            }
            target = target.parentNode;
        }
    }

    // ----------- main code ----------------

    let tk;

    document.addEventListener("DOMContentLoaded", (event) => {
        verovio.module.onRuntimeInitialized = async () => {
            tk = new verovio.toolkit();
            console.log("Verovio has loaded!");
//             console.log("Verovio default options:", tk.getDefaultOptions());
            tk.setOptions({
//                 breaks: "none",
                scale: 30,
//                 landscape: true,
                scaleToPageSize: true,
                pageWidth: 1000,
//                 adjustPageWidth: true,
//                 adjustPageHeight: true
            });
//             console.log("Verovio options:", tk.getOptions());
            initialScore = document.getElementById("initialScore");
            gScoreMei = htmlDecode(initialScore.text.trim())
            if (gScoreMei != "") {
                renderMusic()
                await replaceDataUrl(downloadMEITag, gScoreMei, 'Score.mei');
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
