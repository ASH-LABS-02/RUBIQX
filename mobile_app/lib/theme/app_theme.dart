import 'package:flutter/material.dart';

/// Light, high-contrast "mission control" palette -- deliberately matching
/// the GCS web dashboard's own light-mode chrome so a rescue team switching
/// between the Pi's screen and their phone sees one consistent system, not
/// two unrelated-looking tools. Same accent hues as the original dark
/// build, re-stepped for contrast on white rather than on near-black --
/// swapping only the values here, never the names, is what keeps every
/// other screen in this app working unmodified.
class AppColors {
  AppColors._();

  static const bg = Color(0xFFF6F8FB);
  static const surface = Color(0xFFFFFFFF);
  static const surfaceRaised = Color(0xFFFFFFFF);
  static const surfaceSunken = Color(0xFFF0F3F7);
  static const line = Color(0xFFE2E8F0);
  static const lineFaint = Color(0xFFEEF2F6);

  static const text = Color(0xFF16202E);
  static const textDim = Color(0xFF5B6B80);
  static const dim = Color(0xFF94A3B8);

  static const cyan = Color(0xFF0891B2);
  static const cyanDim = Color(0xFFCFFAFE);
  static const amber = Color(0xFFC2760C);
  static const red = Color(0xFFDC2626);
  static const green = Color(0xFF15803D);
  static const violet = Color(0xFF6D28D9);

  /// Status -> colour, kept in exactly one place so every screen agrees.
  static Color forStatus(String status) => switch (status) {
        'new' => red,
        'acknowledged' => amber,
        'resolved' => green,
        'false_alarm' => dim,
        _ => dim,
      };
}

/// A soft, layered shadow rather than Material's default flat elevation --
/// this one detail does more for a "premium" feel than almost anything
/// else, because flat UI with no shadow reads as unfinished. Lighter
/// default opacity than a dark-UI shadow would use: on white, the same
/// 0.28 reads as heavy/muddy rather than soft -- light UI shadows need to
/// stay closer to a hint than a silhouette.
List<BoxShadow> cardShadow({double opacity = 0.08}) => [
      BoxShadow(
        color: Colors.black.withValues(alpha: opacity),
        blurRadius: 20,
        offset: const Offset(0, 8),
        spreadRadius: -6,
      ),
      BoxShadow(
        color: Colors.black.withValues(alpha: opacity * 0.6),
        blurRadius: 6,
        offset: const Offset(0, 2),
      ),
    ];

List<BoxShadow> glowShadow(Color color, {double opacity = 0.22}) => [
      BoxShadow(
        color: color.withValues(alpha: opacity),
        blurRadius: 24,
        spreadRadius: -4,
      ),
    ];

const radiusSm = 8.0;
const radiusMd = 14.0;
const radiusLg = 20.0;

/// Font roles, used consistently rather than picked ad hoc per screen:
///   display  -- SpaceGrotesk  -- headings, the wordmark, big numbers
///   body     -- Inter         -- everything you read: labels, buttons, copy
///   data     -- JetBrainsMono -- coordinates, timestamps, RSSI, anything
///                                that is literally a measurement
class AppFonts {
  AppFonts._();
  static const display = 'SpaceGrotesk';
  static const body = 'Inter';
  static const data = 'JetBrainsMono';
}

ThemeData buildAppTheme() {
  final base = ThemeData.light(useMaterial3: true);

  final textTheme = base.textTheme
      .apply(bodyColor: AppColors.text, displayColor: AppColors.text)
      .copyWith(
        displayLarge: const TextStyle(
            fontFamily: AppFonts.display, fontWeight: FontWeight.w700, letterSpacing: -0.5),
        headlineMedium: const TextStyle(
            fontFamily: AppFonts.display, fontWeight: FontWeight.w700, letterSpacing: -0.3),
        titleLarge: const TextStyle(
            fontFamily: AppFonts.display, fontWeight: FontWeight.w600, letterSpacing: -0.2),
        titleMedium: const TextStyle(fontFamily: AppFonts.body, fontWeight: FontWeight.w600),
        bodyLarge: const TextStyle(fontFamily: AppFonts.body),
        bodyMedium: const TextStyle(fontFamily: AppFonts.body),
        labelLarge: const TextStyle(fontFamily: AppFonts.body, fontWeight: FontWeight.w600),
      );

  return base.copyWith(
    scaffoldBackgroundColor: AppColors.bg,
    colorScheme: base.colorScheme.copyWith(
      primary: AppColors.cyan,
      secondary: AppColors.amber,
      error: AppColors.red,
      surface: AppColors.surface,
      onSurface: AppColors.text,
    ),
    textTheme: textTheme,
    appBarTheme: const AppBarTheme(
      backgroundColor: AppColors.bg,
      foregroundColor: AppColors.text,
      elevation: 0,
      centerTitle: false,
      scrolledUnderElevation: 0,
      titleTextStyle: TextStyle(
        fontFamily: AppFonts.display,
        fontWeight: FontWeight.w700,
        fontSize: 19,
        letterSpacing: -0.2,
        color: AppColors.text,
      ),
    ),
    cardTheme: CardThemeData(
      color: AppColors.surface,
      elevation: 0,
      margin: EdgeInsets.zero,
      shape: RoundedRectangleBorder(
        borderRadius: BorderRadius.circular(radiusMd),
        side: const BorderSide(color: AppColors.line, width: 1),
      ),
    ),
    navigationBarTheme: NavigationBarThemeData(
      height: 68,
      backgroundColor: AppColors.surface,
      surfaceTintColor: Colors.transparent,
      indicatorColor: AppColors.cyan.withValues(alpha: 0.16),
      indicatorShape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(radiusSm)),
      labelTextStyle: WidgetStateProperty.resolveWith((states) => TextStyle(
            fontFamily: AppFonts.body,
            fontSize: 11,
            fontWeight: states.contains(WidgetState.selected) ? FontWeight.w600 : FontWeight.w500,
            color: states.contains(WidgetState.selected) ? AppColors.cyan : AppColors.dim,
          )),
      iconTheme: WidgetStateProperty.resolveWith((states) => IconThemeData(
            color: states.contains(WidgetState.selected) ? AppColors.cyan : AppColors.dim,
            size: 24,
          )),
    ),
    filledButtonTheme: FilledButtonThemeData(
      style: FilledButton.styleFrom(
        backgroundColor: AppColors.cyan,
        foregroundColor: AppColors.bg,
        textStyle: const TextStyle(fontFamily: AppFonts.body, fontWeight: FontWeight.w700),
        padding: const EdgeInsets.symmetric(vertical: 15),
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(radiusSm)),
      ),
    ),
    outlinedButtonTheme: OutlinedButtonThemeData(
      style: OutlinedButton.styleFrom(
        foregroundColor: AppColors.text,
        side: const BorderSide(color: AppColors.line),
        textStyle: const TextStyle(fontFamily: AppFonts.body, fontWeight: FontWeight.w600),
        padding: const EdgeInsets.symmetric(vertical: 14),
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(radiusSm)),
      ),
    ),
    textButtonTheme: TextButtonThemeData(
      style: TextButton.styleFrom(
        foregroundColor: AppColors.cyan,
        textStyle: const TextStyle(fontFamily: AppFonts.body, fontWeight: FontWeight.w600),
      ),
    ),
    inputDecorationTheme: InputDecorationTheme(
      filled: true,
      fillColor: AppColors.surfaceSunken,
      contentPadding: const EdgeInsets.symmetric(horizontal: 14, vertical: 14),
      labelStyle: const TextStyle(fontFamily: AppFonts.body, color: AppColors.textDim, fontSize: 13),
      hintStyle: const TextStyle(fontFamily: AppFonts.data, color: AppColors.dim, fontSize: 12),
      border: OutlineInputBorder(
        borderRadius: BorderRadius.circular(radiusSm),
        borderSide: const BorderSide(color: AppColors.line),
      ),
      enabledBorder: OutlineInputBorder(
        borderRadius: BorderRadius.circular(radiusSm),
        borderSide: const BorderSide(color: AppColors.line),
      ),
      focusedBorder: OutlineInputBorder(
        borderRadius: BorderRadius.circular(radiusSm),
        borderSide: const BorderSide(color: AppColors.cyan, width: 1.4),
      ),
    ),
    dividerTheme: const DividerThemeData(color: AppColors.line, thickness: 1, space: 1),
    splashFactory: InkSparkle.splashFactory,
    scrollbarTheme: ScrollbarThemeData(
      thumbColor: WidgetStateProperty.all(AppColors.line),
    ),
  );
}
