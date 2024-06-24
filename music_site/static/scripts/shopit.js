    async function processMusicFromResponse(resp) {
        // We do three things with the MusicXML from the response:

        // 1. We stash it off so we can upload it later.
        jsonBody = await resp.json();
        gScoreMusicXml = jsonBody['musicxml'];
        gScoreHumdrum = jsonBody['humdrum'];
        gScoreMei = jsonBody['mei']

        // 2. We draw it on the webpage.
        let svg = tk.renderData(gScoreMei, {});
        document.getElementById("notation").innerHTML = svg;

        // 3. We insert them as data URLs in the download anchor tags.
        await replaceDataUrl(downloadMusicXMLTag, gScoreMusicXml, 'Shopped.musicxml');
        await replaceDataUrl(downloadHumdrumTag, gScoreHumdrum, 'Shopped.krn');
        await replaceDataUrl(downloadMEITag, gScoreMei, 'Shopped.mei');
    }

    function transpose() {
        formData = new FormData();
        formData.append('command', 'transpose');
        formData.append('semitones', +2);  // 2 semitones: a whole tone up
        formData.append('score', gScoreHumdrum);
        formData.append('format', 'humdrum')
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
        formData.append('score', gScoreHumdrum);
        formData.append('format', 'humdrum')
        fetch(
            '/command',
            {method: 'POST', body: formData }
        ).then( async (resp) => {
            await processMusicFromResponse(resp);
        });
    }

    function shopItUpper() {
        shopIt('UpperVoices')
    }

    function shopItLower() {
        shopIt('LowerVoices')
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
            formData.append('file', selectedFile)
            formData.append('filename', selectedFile.name);
            const resp = await fetch(
                '/score',
                {method: 'POST', body: formData }
            );
            await processMusicFromResponse(resp);
        };
        reader.readAsBinaryString(selectedFile);
    }

    // ----------- main code ----------------

    let tk;

    document.addEventListener("DOMContentLoaded", (event) => {
        verovio.module.onRuntimeInitialized = () => {
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
            })
//             console.log("Verovio options:", tk.getOptions());
        }
    });

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

    let gScore;
    let gScoreEl;
    let gScoreMusicXml;  // for downloading
    let gScoreHumdrum;   // for uploading with commands (it's way smaller)
    let gScoreMei;       // for rendering with verovio

    transposeBtn.addEventListener("click", transpose);
    shopItUpperBtn.addEventListener("click", shopItUpper)
    shopItLowerBtn.addEventListener("click", shopItLower)
