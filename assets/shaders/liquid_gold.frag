#version 120

uniform vec2 u_resolution;
uniform float u_time;
uniform float u_amplitude;
uniform float u_smoothed_amplitude;
uniform float u_low_energy;
uniform float u_mid_energy;
uniform float u_high_energy;
uniform float u_peak_impulse;

uniform float u_listening;
uniform float u_thinking;
uniform float u_speaking;
uniform float u_error;
uniform float u_disabled;

uniform float u_motion;
uniform float u_droplets;
uniform float u_max_steps;

varying vec2 v_uv;

const float PI = 3.14159265359;

mat2 rotate2d(float angle)
{
    float c = cos(angle);
    float s = sin(angle);
    return mat2(c, -s, s, c);
}

float sphereSdf(vec3 p, float radius)
{
    return length(p) - radius;
}

float ellipsoidSdf(vec3 p, vec3 radii)
{
    vec3 safeRadii = max(radii, vec3(0.025));
    float k0 = length(p / safeRadii);
    float k1 = length(p / (safeRadii * safeRadii));
    return k0 * (k0 - 1.0) / max(k1, 0.0001);
}

float capsuleSdf(vec3 p, vec3 a, vec3 b, float radius)
{
    vec3 pa = p - a;
    vec3 ba = b - a;
    float h = clamp(dot(pa, ba) / dot(ba, ba), 0.0, 1.0);
    return length(pa - ba * h) - radius;
}

float smoothUnion(float a, float b, float softness)
{
    float h = clamp(0.5 + 0.5 * (b - a) / softness, 0.0, 1.0);
    return mix(b, a, h) - softness * h * (1.0 - h);
}

float fluidNoise(vec3 p, float t)
{
    // Three broad waves are enough to keep the surface alive.  Avoiding a
    // many-octave noise stack is the main reason this shader remains usable
    // on integrated Windows GPUs.
    float a = sin(p.x * 2.73 + t * 0.81);
    float b = sin(p.y * 3.17 - t * 0.63 + a * 0.72);
    float c = sin(p.z * 3.61 + t * 0.47 + b * 0.58);
    return a * b * 0.68 + c * 0.32;
}

float liquidSdf(vec3 p)
{
    float audioActive = max(u_listening, u_speaking);
    float voice = u_smoothed_amplitude;
    float speed = 0.16
        + u_listening * (0.30 + voice * 0.85)
        + u_thinking * 0.48
        + u_speaking * (0.58 + voice * 1.25)
        - u_disabled * 0.13;
    float t = u_time * max(0.025, speed) * u_motion;

    p.xy *= rotate2d(t * (0.14 + u_thinking * 0.54));
    p.xz *= rotate2d(-t * (0.08 + audioActive * 0.12));

    float compact = 1.0 - u_error * 0.16 - u_disabled * 0.28;
    float audioSpread = audioActive * (voice * 0.16 + u_peak_impulse * 0.07);
    float thinkingPulse = u_thinking * (0.94 + 0.06 * sin(u_time * 2.1));
    float spread = compact * mix(1.0, thinkingPulse, u_thinking) + audioSpread;

    vec3 c0 = vec3(
        -0.32 + 0.075 * sin(t * 1.31),
         0.29 + 0.075 * sin(t * 0.83 + 1.2),
         0.06 * cos(t * 1.17)
    ) * spread;
    vec3 c1 = vec3(
         0.34 + 0.080 * sin(t * 0.97 + 2.4),
         0.24 + 0.065 * cos(t * 1.43),
        -0.08 + 0.055 * sin(t * 0.71)
    ) * spread;
    vec3 c2 = vec3(
        -0.29 + 0.065 * cos(t * 1.11),
        -0.31 + 0.080 * sin(t * 1.27 + 0.4),
        -0.07 * cos(t * 0.61)
    ) * spread;
    vec3 c3 = vec3(
         0.36 + 0.065 * cos(t * 1.29 + 0.8),
        -0.27 + 0.065 * sin(t * 0.89),
         0.09 * sin(t * 1.03)
    ) * spread;
    vec3 c4 = vec3(
         0.01 + 0.075 * sin(t * 0.69),
         0.02 + 0.065 * cos(t * 1.19),
         0.24 + 0.045 * sin(t * 0.91)
    ) * compact;

    float syllable = audioActive * (
        0.035 * sin(u_time * (5.0 + u_mid_energy * 9.0)) * voice
        + 0.055 * u_peak_impulse
    );
    float r0 = 0.265 + 0.022 * sin(t * 1.73) + u_low_energy * 0.060 + syllable;
    float r1 = 0.245 + 0.024 * cos(t * 1.37) + u_mid_energy * 0.052;
    float r2 = 0.275 + 0.021 * sin(t * 1.51 + 2.0) + u_low_energy * 0.048;
    float r3 = 0.225 + 0.023 * cos(t * 1.91) + u_mid_energy * 0.045;
    float r4 = 0.205 + 0.018 * sin(t * 2.07) + voice * audioActive * 0.030;
    float radiusScale = 1.0 - u_error * 0.10 - u_disabled * 0.12;

    float upper = smoothUnion(
        ellipsoidSdf(p - c0, vec3(r0 * 1.28, r0 * 0.72, r0 * 0.90) * radiusScale),
        ellipsoidSdf(p - c1, vec3(r1 * 0.78, r1 * 1.30, r1 * 0.88) * radiusScale),
        0.082 + voice * audioActive * 0.035
    );
    upper = smoothUnion(
        upper,
        capsuleSdf(p, c0, c1, 0.068 + voice * audioActive * 0.025),
        0.052
    );

    float lower = smoothUnion(
        ellipsoidSdf(p - c2, vec3(r2 * 0.76, r2 * 1.28, r2 * 0.90) * radiusScale),
        ellipsoidSdf(p - c3, vec3(r3 * 1.34, r3 * 0.70, r3 * 0.92) * radiusScale),
        0.078 + voice * audioActive * 0.030
    );
    lower = smoothUnion(
        lower,
        capsuleSdf(p, c2, c3, 0.062 + u_low_energy * audioActive * 0.022),
        0.048
    );

    float core = ellipsoidSdf(
        p - c4, vec3(r4 * 0.70, r4 * 1.34, r4 * 0.82) * radiusScale
    );
    core = smoothUnion(
        core,
        capsuleSdf(p, c1, c4, 0.052 + u_mid_energy * audioActive * 0.018),
        0.042
    );
    core = smoothUnion(
        core,
        capsuleSdf(p, c4, c2, 0.049 + u_low_energy * audioActive * 0.016),
        0.038
    );

    // Keep several independent masses instead of turning the composition into
    // a solid ball. The narrow unions create transient liquid membranes.
    float d = min(upper, min(lower, core));

    // Two foreground filaments form and dissolve between otherwise separate
    // masses.  Their shallow depth keeps them visible as liquid membranes
    // instead of hiding the joins inside the larger lobes.
    vec3 filamentOffset = vec3(0.0, 0.0, 0.27 + voice * audioActive * 0.05);
    float filamentA = capsuleSdf(
        p, c0 + vec3(0.08, -0.08, 0.0) + filamentOffset,
        c3 + vec3(-0.10, 0.10, 0.0) + filamentOffset,
        0.030 + u_mid_energy * audioActive * 0.018
    );
    float filamentB = capsuleSdf(
        p, c1 + vec3(-0.10, -0.10, 0.0) + filamentOffset,
        c2 + vec3(0.10, 0.10, 0.0) + filamentOffset,
        0.024 + u_high_energy * audioActive * 0.014
    );
    d = smoothUnion(d, min(filamentA, filamentB), 0.022);

    vec3 voidA = vec3(-0.14, 0.29, 0.24);
    vec3 voidB = vec3(0.13, -0.27, 0.22);
    float holeA = ellipsoidSdf(
        p - voidA, vec3(0.12, 0.17 + 0.018 * sin(t * 1.07), 0.18)
    );
    float holeB = ellipsoidSdf(
        p - voidB, vec3(0.15 + 0.016 * cos(t * 1.23), 0.11, 0.17)
    );
    d = max(d, -min(holeA, holeB));

    float deformation = 0.032
        + audioActive * (0.014 + voice * 0.040)
        + u_thinking * 0.020
        + u_peak_impulse * audioActive * 0.024;
    d += fluidNoise(p * (2.05 + u_high_energy * audioActive * 0.55), t) * deformation;

    // The spherical boundary is never rendered; it only prevents peaks from
    // escaping the intended floating composition during loud syllables.
    d = max(d, sphereSdf(p, 0.94 + voice * audioActive * 0.035));

    if (u_droplets > 0.5) {
        float dropActivity = 0.55 + audioActive * (0.20 + voice * 0.50);
        vec3 d0 = vec3(
            -0.57 + 0.055 * sin(t * 1.7),
             0.49 + 0.045 * cos(t * 1.2),
             0.03
        ) * dropActivity / 0.75;
        vec3 d1 = vec3(
             0.59 + 0.050 * cos(t * 1.1),
            -0.43 + 0.045 * sin(t * 1.8),
            -0.04
        ) * dropActivity / 0.75;
        vec3 d2 = vec3(
             0.51 + 0.040 * sin(t * 1.4),
             0.54 + 0.040 * cos(t * 1.6),
             0.08
        ) * dropActivity / 0.75;
        float drops = sphereSdf(p - d0, 0.038 + u_peak_impulse * audioActive * 0.020);
        drops = min(drops, sphereSdf(p - d1, 0.030 + u_high_energy * audioActive * 0.018));
        drops = min(drops, sphereSdf(p - d2, 0.024 + voice * audioActive * 0.014));
        d = min(d, drops);
    }

    return d;
}

vec3 liquidNormal(vec3 p)
{
    const float e = 0.0032;
    vec2 h = vec2(1.0, -1.0) * 0.5773;
    return normalize(
        h.xyy * liquidSdf(p + h.xyy * e) +
        h.yyx * liquidSdf(p + h.yyx * e) +
        h.yxy * liquidSdf(p + h.yxy * e) +
        h.xxx * liquidSdf(p + h.xxx * e)
    );
}

vec3 shadeGold(vec3 p, vec3 normal, vec3 rayDirection)
{
    vec3 viewDirection = normalize(-rayDirection);
    vec3 keyLight = normalize(vec3(-0.58, 0.74, 0.49));
    vec3 rimLight = normalize(vec3(0.72, -0.16, 0.67));
    vec3 fillLight = normalize(vec3(-0.12, -0.08, 1.0));

    float keyDiffuse = max(dot(normal, keyLight), 0.0);
    float rimDiffuse = max(dot(normal, rimLight), 0.0);
    float fillDiffuse = max(dot(normal, fillLight), 0.0);
    float fresnel = pow(1.0 - max(dot(normal, viewDirection), 0.0), 4.5);
    float roughness = 0.19 + u_disabled * 0.20 + u_error * 0.09;
    float keySpec = pow(max(dot(reflect(-keyLight, normal), viewDirection), 0.0),
                        mix(92.0, 34.0, roughness));
    float rimSpec = pow(max(dot(reflect(-rimLight, normal), viewDirection), 0.0),
                        mix(60.0, 24.0, roughness));

    float vertical = clamp(p.y * 0.5 + 0.5, 0.0, 1.0);
    float surfaceVariation = 0.5 + 0.5 * fluidNoise(p * 2.3, u_time * 0.11);
    vec3 deepGold = vec3(0.16, 0.050, 0.004);
    vec3 warmGold = vec3(1.00, 0.58, 0.055);
    vec3 brightGold = vec3(1.00, 0.88, 0.48);
    vec3 base = mix(deepGold, warmGold, 0.48 + vertical * 0.25 + surfaceVariation * 0.15);

    vec3 color = base * (0.36 + keyDiffuse * 0.68 + rimDiffuse * 0.20 + fillDiffuse * 0.28);
    color += brightGold * keySpec * (1.65 + u_smoothed_amplitude * u_speaking * 0.75);
    color += vec3(1.0, 0.92, 0.68) * rimSpec * 0.82;
    color += mix(warmGold, brightGold, 0.65) * fresnel * 0.72;
    color += vec3(0.12, 0.038, 0.005) * max(-normal.y, 0.0) * 0.35;

    color *= 1.0 - u_error * 0.30 - u_disabled * 0.52;
    color = color / (vec3(1.0) + color * 0.48);
    return pow(max(color, vec3(0.0)), vec3(0.86));
}

void main()
{
    vec2 screen = (gl_FragCoord.xy / u_resolution.xy) * 2.0 - 1.0;
    screen.x *= u_resolution.x / u_resolution.y;

    vec3 rayOrigin = vec3(0.0, 0.0, 2.72);
    vec3 rayDirection = normalize(vec3(screen * 0.96, -2.38));
    float sphereProjection = dot(rayOrigin, rayDirection);
    float sphereDiscriminant = sphereProjection * sphereProjection
                             - dot(rayOrigin, rayOrigin) + 1.08 * 1.08;
    float travel = 0.0;
    float farTravel = -1.0;
    float distanceToSurface = 1.0;
    bool hit = false;

    if (sphereDiscriminant > 0.0) {
        float sphereRoot = sqrt(sphereDiscriminant);
        travel = max(0.0, -sphereProjection - sphereRoot);
        farTravel = -sphereProjection + sphereRoot;
    }

    for (int i = 0; i < 72; ++i) {
        if (float(i) >= u_max_steps) {
            break;
        }
        vec3 point = rayOrigin + rayDirection * travel;
        distanceToSurface = liquidSdf(point);
        if (distanceToSurface < 0.0022) {
            hit = true;
            break;
        }
        travel += max(distanceToSurface * 0.82, 0.0035);
        if (travel > farTravel) {
            break;
        }
    }

    if (!hit) {
        // A tiny contact shadow separates the floating metal from both bright
        // and dark desktops without outlining the invisible spherical limit.
        vec2 shadowPoint = screen - vec2(0.0, -0.72);
        float shadow = exp(-12.0 * (shadowPoint.x * shadowPoint.x
                                 + shadowPoint.y * shadowPoint.y * 5.0));
        float glow = exp(-5.5 * dot(screen, screen))
                   * (0.018 + u_smoothed_amplitude * max(u_listening, u_speaking) * 0.028);
        float alpha = shadow * 0.16 + glow;
        gl_FragColor = vec4(vec3(0.055, 0.035, 0.012) * alpha, alpha);
        return;
    }

    vec3 point = rayOrigin + rayDirection * travel;
    vec3 normal = liquidNormal(point);
    vec3 gold = shadeGold(point, normal, rayDirection);
    gl_FragColor = vec4(gold, 1.0);
}
