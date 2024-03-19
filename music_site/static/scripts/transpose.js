    async function processMusicFromResponse(resp) {
        // We do three things with the MusicXML from the response:

        // 1. We stash it off so we can upload it later.
        jsonBody = await resp.json();
        gScoreMusicXml = jsonBody['musicxml'];
        gScoreHumdrum = jsonBody['humdrum'];

        // 2. We draw it on the webpage.
        const sp = new music21.musicxml.xmlToM21.ScoreParser();
        gScore = sp.scoreFromText(gScoreMusicXml);
        if (gScoreEl === undefined) {
            gScoreEl = gScore.appendNewDOM();
        } else {
            gScoreEL = gScore.replaceDOM()
        }

        // 3. We insert it as a data URL in the download anchor tag.
        await replaceDataUrl(downloadAnchorTag, gScoreMusicXml);
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

    async function bytesToBase64DataUrl(bytes, type = "application/octet-stream") {
        return await new Promise((resolve, reject) => {
            const reader = Object.assign(new FileReader(), {
                onload: () => resolve(reader.result),
                onerror: () => reject(reader.error),
            });
            reader.readAsDataURL(new File([bytes], "", { type }));
        });
    }

    async function replaceDataUrl(anchorTag, text) {
        anchorTag.download = "Processed.musicxml";
        anchorTag.href = await bytesToBase64DataUrl(text);
    }

    function handleFiles() {
        // Assumes only one MusicXML file (since our HTML only allows one).
        // POSTs to server to be saved for later processing; displays the
        // resulting MusicXML (from response), and adds a download data URL
        // to the DOM for the user to save off that MusicXML).
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

    const transposeBtn = document.querySelector('#transpose');
    const downloadAnchorTag = document.querySelector('#downloadAnchor');
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
    let gScoreMusicXml;  // for drawing
    let gScoreHumdrum;   // for uploading with commands (it's way smaller)

    transposeBtn.addEventListener("click", transpose);
