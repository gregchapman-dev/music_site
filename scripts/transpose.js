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

    function replaceDataUrl(anchorTag, text) {
        anchorTag.download = "Processed.musicxml"
        anchorTag.href = "data:text/plain," + text;
    }

    function handleFiles(files) {
        // Assumes only one MusicXML file (since our HTML only allows one).
        // POSTs to server to be saved for later processing; displays the
        // resulting MusicXML (from response), and adds a download data URL
        // to the DOM for the user to save off that MusicXML).
        if files === undefined {
            // assume this is called from an <input> element, so the files
            // will be in this.files
            files = this.files
        }
        const selectedFile = files[0];
        const reader = new FileReader();
        reader.onload = (e) => {
            // POST file contents to '/music_engine/score'
            // The response will be the MusicXML equivalent of the score file
            // contents you POSTed (converted by server, even if what you
            // POSTed was MusicXML).
            const contentsDict = {'score': reader.contents, 'filename': selectedFile.name}
            const resp = await fetch('/music_engine/score', 'POST', contentsDict.jsonify());
            const jsonBody = resp.json();
            scoreXml = jsonBody['musicxml'];
            const sp = new music21.musicxml.xmlToM21.ScoreParser();
            score = sp.scoreFromText(scoreXml);
            scoreEl = sc.appendNewDOM();
            replaceDataUrl(downloadAnchorTag, scoreXml);
        }
        reader.readAsDataURL(file);
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
