    function transpose() {
        const req = new XMLHttpRequest();

        req.onload = (e) => {
          const arraybuffer = req.response; // not responseText
          /* … */
        };
        req.open('PUT', '');
        req.responseType = "arraybuffer";
        req.send();
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

            const jsonBody = await resp.json();
            scoreXml = jsonBody['musicxml'];
            const sp = new music21.musicxml.xmlToM21.ScoreParser();
            score = sp.scoreFromText(scoreXml);
            scoreEl = score.appendNewDOM();
            await replaceDataUrl(downloadAnchorTag, scoreXml);
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

    let score;
    let scoreEl;
    let scoreStr;

//     mxUrl = 'scores/bachOut.xml';
//     let sp = new music21.musicxml.xmlToM21.ScoreParser();
//     const scorePromise = sp.scoreFromUrl(mxUrl)
//
//     scorePromise.then((sc) => {
//         score = sc;
//         scoreEl = sc.appendNewDOM();
//         scoreStr = score.write();
//         replaceDataUrl(downloadAnchorTag, scoreStr);
//     })

    transposeBtn.addEventListener("click", transpose);
