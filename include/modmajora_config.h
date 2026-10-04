#ifndef MODMAJORA_CONFIG_H
#define MODMAJORA_CONFIG_H

/**
 * Build target and feature configuration.
 *
 * The build target is selected with `make TARGET=...` (see docs/recomp.md):
 *   - TARGET_N64:    building the N64 ROM (`make`, the default)
 *   - TARGET_RECOMP: building a Majora's Mask: Recompiled mod (`make nrm`)
 *
 * Code that only makes sense for one target can be wrapped with `#if TARGET_N64` / `#if TARGET_RECOMP`.
 *
 * Features are toggles for groups of changes, each with a default value per target.
 * Wrap the code of a feature with `#if FEATURE(NAME) ... #endif`.
 * This is useful for changes that overlap with features that recomp already provides
 * (camera controls, widescreen, framerate, ...): enable them for the N64 and disable them for recomp.
 *
 * Any feature can be overridden from the command line, for example `make nrm FEATURE_CUSTOM_CAMERA=1`.
 *
 * Note: vanilla code must not depend on these, so that the matching build is unaffected.
 */

#if !defined(TARGET_N64) && !defined(TARGET_RECOMP)
#define TARGET_N64 1
#endif

#ifndef TARGET_N64
#define TARGET_N64 0
#endif
#ifndef TARGET_RECOMP
#define TARGET_RECOMP 0
#endif

#if TARGET_N64 && TARGET_RECOMP
#error "Only one of TARGET_N64 and TARGET_RECOMP can be set"
#endif

// Selects a feature's default value for the current target
#define FEATURE_DEFAULT(n64, recomp) (TARGET_RECOMP ? (recomp) : (n64))

// Use in `#if FEATURE(NAME)`.
// Note: like any undefined macro in `#if`, an undeclared feature (e.g. a typo) silently evaluates to 0.
#define FEATURE(name) (FEATURE_##name)

/**
 * Feature declarations.
 *
 * To add a feature `NAME`, copy this block:
 *
 * #ifdef FEATURE_NAME_OVERRIDE
 * #define FEATURE_NAME FEATURE_NAME_OVERRIDE
 * #else
 * #define FEATURE_NAME FEATURE_DEFAULT(n64 value, recomp value)
 * #endif
 *
 * Example: a custom camera control that would conflict with the camera options of recomp,
 * so it is only enabled by default on N64:
 *
 * #ifdef FEATURE_CUSTOM_CAMERA_OVERRIDE
 * #define FEATURE_CUSTOM_CAMERA FEATURE_CUSTOM_CAMERA_OVERRIDE
 * #else
 * #define FEATURE_CUSTOM_CAMERA FEATURE_DEFAULT(1, 0)
 * #endif
 */

// (no features declared yet)

#endif
