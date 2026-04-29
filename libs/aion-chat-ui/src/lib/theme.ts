export const AION_THEME = {
	colors: {
		brandAccent: "#C5AFFF",
		brandPrimary: "#816CFF",
		textWarm: "#FAF8F3",
		appBackground: "#05070C",
		panelBackground: "#2A2F36",
		textPrimary: "#F5F7FA",
		textPlaceholder: "#C2C8D0",
		textStrong: "#FFFFFF",
		textMuted: "#8B96A5",
		selection: "#715CFF"
	}
} as const;

export const HOME_THEME = {
	ansiFallback: AION_THEME.colors.textWarm,
	logoAccent: AION_THEME.colors.brandAccent,
	logoPrimary: AION_THEME.colors.brandPrimary,
	logoText: AION_THEME.colors.textWarm,
	logoCutout: AION_THEME.colors.appBackground,
	frame: AION_THEME.colors.brandAccent,
	panelText: AION_THEME.colors.textWarm,
	summaryText: AION_THEME.colors.textWarm
} as const;

export const COMPOSER_THEME = {
	background: AION_THEME.colors.panelBackground,
	foreground: AION_THEME.colors.textPrimary,
	placeholder: AION_THEME.colors.textPlaceholder,
	accent: AION_THEME.colors.textStrong,
	muted: AION_THEME.colors.textMuted,
	selection: AION_THEME.colors.selection
} as const;

export const MESSAGE_THEME = {
	background: AION_THEME.colors.panelBackground,
	foreground: AION_THEME.colors.textPrimary,
	accent: AION_THEME.colors.textStrong,
	muted: AION_THEME.colors.textMuted,
	labelAccent: AION_THEME.colors.brandAccent
} as const;

export const CONNECTION_THEME = {
	connected: "green",
	connecting: "yellow",
	error: "red"
} as const;

export const STATUS_BAR_THEME = {
	border: "gray"
} as const;

export const MARKDOWN_THEME = {
	codeBorder: "yellow",
	codeText: "yellow"
} as const;
