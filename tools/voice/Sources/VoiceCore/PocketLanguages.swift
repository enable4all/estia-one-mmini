import Foundation

/// jane in six more languages (owner, 25 Oct: "pocket does more languages than English right... lets use those, leave
/// koroko for the ones that Pocket does not do", and after eve and jane in each: "jane jane, lets have her as default
/// for those languages"): Spanish, Italian, Portuguese, Dutch, German and French in Pocket TTS's full model of each
/// language (the 100M architecture, never the 24-layer previews), int8, one thread, as English; Korean, Polish,
/// Japanese and Russian, which Pocket has no model for, stay Supertonic-3's F1. The bridge names jane with the language
/// (`config.POCKET_LANGUAGES`); any other voice in these languages is Supertonic-3's as before. Each language's files
/// are fetched only for the language in use ("the pocket language models we download in Estia only if they are
/// requested right?"): its speaker is idle at every start and the first line asked in it starts its check (and its
/// fetch, about 227 MB, when a file is missing); until it is ready, and for any line it cannot say, Supertonic-3's F1
/// says it (`standIn(for:voice:)`).
extension PocketTTS {
    /// The voice the bridge names in these languages: only jane's states are pinned for them.
    public static let languageVoice = "jane"

    public static let languages: [PocketSpeech] = [
        PocketSpeech(voice: languageVoice, model: .spanish, speaks: languageVoice, health: "pocket_es", language: "es"),
        PocketSpeech(voice: languageVoice, model: .italian, speaks: languageVoice, health: "pocket_it", language: "it"),
        PocketSpeech(voice: languageVoice, model: .portuguese, speaks: languageVoice, health: "pocket_pt", language: "pt"),
        PocketSpeech(voice: languageVoice, model: .dutch, speaks: languageVoice, health: "pocket_nl", language: "nl"),
        PocketSpeech(voice: languageVoice, model: .german, speaks: languageVoice, health: "pocket_de", language: "de"),
        PocketSpeech(voice: languageVoice, model: .french, speaks: languageVoice, health: "pocket_fr", language: "fr"),
    ]

    /// The English Pocket voices setup gives a male name (owner, 26 Oct: "alba & michael for males").
    public static let maleVoices: Set<String> = ["alba", "michael"]
    /// Kokoro's male voice: it says the lines alba and michael cannot (setup's male voice before 26 Oct).
    public static let maleStandIn = "am_michael"

    /// Who says a line Pocket cannot in `voice`: in English Kokoro's voice of its kind (am_michael for alba and
    /// michael, af_heart for heart, jane, eve and vera: the review of 26 Oct, R1-F1, a male-voiced call heard a woman's
    /// line), in another language Supertonic-3's F1 (the voice it has always spoken them in).
    public static func standIn(for language: String, voice: String) -> String {
        guard language == Languages.english else { return "F1" }
        return maleVoices.contains(voice) ? maleStandIn : SpeechRequest.defaultVoice
    }
}

extension PocketModel {
    /// The revision of the weights the release program names for the six (1 Oct, "the retrained languages"); jane's
    /// states were recomputed with them in that upload and are unchanged since (the same bytes at the program's voice
    /// revision a3055dcb and at main a7056f5, 25 Oct): pinned there, the weights' own revision, as English jane's.
    static let languagesRevision = "1e08e6a23401048648a9fdcfde2f89348215c2a7"
    /// The tokenizers' revision the program names (French's own below); the same bytes at main.
    static let tokenizersRevision = "4e1e0a3e611c51c0b4ed8174fc10f32a54644303"  // a public file hash, not a key (gitleaks:allow; privacy-ok: a public sha256)

    /// One language's full model, by the release's name for it: the weights, its tokenizer.json (the config's
    /// `tokenizers`) and jane's state, each by its sha256 computed from the official download (25 Oct; the weights
    /// 219,029,196 bytes each, jane's state 7,325,496).
    static func language(
        _ name: String, weights: String, tokenizer: String, tokenizerAt: String = tokenizersRevision, jane: String,
        gainDB: Double, head: String
    ) -> PocketModel {
        PocketModel(
            name: name,
            weights: pin(languagesRevision, "languages/\(name)", "model.safetensors", weights, "\(name)/weights"),
            tokenizer: pin(tokenizerAt, "languages/\(name)", "tokenizer.json", tokenizer, "\(name)/tokenizer"),
            voices: ["jane": PocketVoice(
                pin: pin(languagesRevision, "languages/\(name)/embeddings", "jane.safetensors", jane, "\(name)/jane"),
                gainDB: gainDB)],
            config: "weights_path: {weights}\n\n" + head + "\n\n" + languageBody,
            maxFileBytes: 256 << 20)
    }

    public static let spanish = language(
        "spanish", weights: "c4988ff376692f2ab1e85b17350755b08f16e49c0b5f0de8b271efa9abd71de7",
        tokenizer: "9ade09bbdc64ab2b59952b90773fa0890b780ece4fd5aef0d36a00c6ad5f0531",  // a public file hash, not a key (gitleaks:allow; privacy-ok: a public sha256)
        jane: "a1f1c36c155e84da0bce58d635128059413e618324d47f9679aa012d8bdeb9a0", gainDB: -1.0, head: spanishHead)
    public static let italian = language(
        "italian", weights: "16365c43eaabee9b222cd414d6a48f0976b4800a21fe5d55253f645eff7eeb1f",
        tokenizer: "4bc1c0d4d54f9480183e94c94cfabf74fd89b0912537d4ef607d1982c4a381ec",  // a public file hash, not a key (gitleaks:allow; privacy-ok: a public sha256)
        jane: "b9787a3a8e3d3e608c493bd24ec2880fe65717807f938a8c1565c126590acdc4", gainDB: -3.5, head: italianHead)
    public static let portuguese = language(
        "portuguese", weights: "28e20f32515ba8bd0eaec69671f11f8921414fcdc375aa072fa09f46cc1057c5",
        tokenizer: "b33e269f7f042a763080d71ba087d74d2f6759674b238a723c028e436b1a25ab",  // a public file hash, not a key (gitleaks:allow; privacy-ok: a public sha256)
        jane: "161d4cc6b023d20207220d3687e42d41847c1f823956062646b0c9e22705805e", gainDB: -1.0, head: portugueseHead)
    public static let dutch = language(
        "dutch", weights: "59b5c3249ab05e8b5fcb0a296e9b0cb0c0a20da5e1fd28c04bb90b2175612aeb",
        tokenizer: "e6f610b98c427641b4ab0e1024dca7843e3d6b620871a14b204932e42c5a1736",  // a public file hash, not a key (gitleaks:allow; privacy-ok: a public sha256)
        jane: "36816d4767fe69a67eeb4d9778c71eea272f34d99d63c2af8c47cf772339b5c1", gainDB: -4.5, head: dutchHead)
    public static let german = language(
        "german", weights: "9fe42605604832349e3f495177bde56b77ae8fee73b2ed4b63236092ccde7621",
        tokenizer: "2d77849811e3d1b6ae80e5b11b203f57e1847901104d8a0573b34f46f8dddb53",  // a public file hash, not a key (gitleaks:allow; privacy-ok: a public sha256)
        jane: "bd1bc91fd38a724b14f5c9cb4d67327f968568beb28d72fe7bb81f0fba75b685", gainDB: -4.5, head: germanHead)
    public static let french = language(
        "french", weights: "56366cfed6c25bfcd3bf4c61e66e06efaf52e78a5d06e1dcade1c588495bc699",
        tokenizer: "6211acffe9cb95172f71f0051f18e26ea305b2b32ea00b331ee976551c70887f",  // a public file hash, not a key (gitleaks:allow; privacy-ok: a public sha256)
        tokenizerAt: "8843db76457a91db32077edf8dfcd1c0e3e755fd",  // a public file hash, not a key (gitleaks:allow; privacy-ok: a public sha256)
        jane: "1482685d8a1a22a7294df650f61293bab353445710e26a60ae057cbf4ad85221", gainDB: -3.5, head: frenchHead)
}

// MARK: - The six configs, as the release program 3.3.0 embeds them (`spanish` ... `french`): each its own head (the
// characters absent from its training data; French and German without semicolons), then one body

private let spanishHead = """
    replace_characters:
      '"': ''
      '“': ''
      '”': ''
      '„': ''
      '«': ''
      '»': ''
      '’': ''''
      '‘': ''''
      '(': ''
      ')': ''
      '[': ''
      ']': ''
      '¡': ''
      '¿': ''
      '–': ','
    """

private let italianHead = """
    replace_characters:
      '"': ''
      '“': ''
      '”': ''
      '„': ''
      '«': ''
      '»': ''
      '’': ''''
      '‘': ''''
      '(': ''
      ')': ''
      '[': ''
      ']': ''
      '–': ','
    """

private let portugueseHead = """
    replace_characters:
      '"': ''
      '“': ''
      '”': ''
      '„': ''
      '«': ''
      '»': ''
      '’': ''''
      '‘': ''''
      '(': ''
      ')': ''
      '[': ''
      ']': ''
      '–': ','
    """

private let dutchHead = """
    replace_characters:
      '"': ''
      '“': ''
      '”': ''
      '„': ''
      '«': ''
      '»': ''
      '’': ''''
      '‘': ''''
      '(': ''
      ')': ''
      '[': ''
      ']': ''
      '–': ','
    """

private let germanHead = """
    remove_semicolons: true
    replace_characters:
      '"': ''
      '“': ''
      '”': ''
      '„': ''
      '«': ''
      '»': ''
      '’': ''''
      '‘': ''''
      '(': ''
      ')': ''
      '[': ''
      ']': ''
      '–': ','
      '—': ','
    """

private let frenchHead = """
    remove_semicolons: true
    # Replace characters that are absent from the training data.
    replace_characters:
      '"': ''
      '“': ''
      '”': ''
      '„': ''
      '«': ''
      '»': ''
      '’': "'"
      '‘': "'"
      ':': ','
      '(': ''
      ')': ''
      '[': ''
      ']': ''
      '–': ','
    """

private let languageBody = """
    flow_lm:
      insert_bos_before_voice: true
      dtype: float32
      flow:
        depth: 6
        dim: 512
      transformer:
        d_model: 1024
        hidden_scale: 4
        max_period: 10000
        num_heads: 16
        num_layers: 6
      lookup_table:
        dim: 1024
        n_bins: 4000
        tokenizer: tokenizers
        tokenizer_path: {tokenizer}
      #weights_path: partial.safetensors

    mimi:
      dtype: float32
      sample_rate: 24000
      inner_dim: 32
      outer_dim: 512
      channels: 1
      frame_rate: 12.5
      seanet:
        dimension: 512
        channels: 1
        n_filters: 64
        n_residual_layers: 1
        ratios:
        - 6
        - 5
        - 4
        kernel_size: 7
        residual_kernel_size: 3
        last_kernel_size: 3
        dilation_base: 2
        pad_mode: constant
        compress: 2
      transformer:
        d_model: 512
        num_heads: 8
        num_layers: 2
        layer_scale: 0.01
        context: 250
        dim_feedforward: 2048
        input_dimension: 512
        output_dimensions:
        - 512
      quantizer:
        dimension: 32
        output_dimension: 512
      #weights_path: codec.safetensors

    """
