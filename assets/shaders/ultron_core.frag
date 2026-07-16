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
uniform float u_opacity;

varying vec2 v_uv;

const float TAU = 6.28318530718;

float ring(float value, float center, float width, float aa)
{
    return 1.0 - smoothstep(width, width + aa, abs(value - center));
}

float dash(float angle, float count, float phase, float gap)
{
    float cell = fract(angle / TAU * count + phase);
    return smoothstep(gap, gap + 0.035, cell)
         * (1.0 - smoothstep(1.0 - gap - 0.035, 1.0 - gap, cell));
}

float ellipseRing(vec2 p, float squash, float center, float width, float aa)
{
    vec2 q = vec2(p.x, p.y / squash);
    return ring(length(q), center, width, aa);
}

void addLight(inout vec3 premul, inout float alpha, vec3 color, float mask)
{
    float amount = clamp(mask, 0.0, 1.0);
    premul += color * amount;
    alpha = max(alpha, amount);
}

void main()
{
    vec2 p = v_uv * 2.0 - 1.0;
    p.x *= u_resolution.x / u_resolution.y;
    float radius = length(p);
    if (radius > 0.94) {
        gl_FragColor = vec4(0.0);
        return;
    }

    float angle = atan(p.y, p.x);
    float aa = 2.15 / min(u_resolution.x, u_resolution.y);
    float voice = clamp(u_smoothed_amplitude, 0.0, 1.0);
    float engaged = max(max(u_listening, u_speaking), u_thinking);
    float energy = clamp(voice * max(u_listening, u_speaking)
                       + u_thinking * 0.30 + u_peak_impulse * 0.35, 0.0, 1.0);
    float speed = (0.72 + engaged * 0.55 + energy * 1.20)
                * max(0.35, u_motion);
    float t = u_time * speed;
    float brightness = (1.0 - u_disabled * 0.68)
                     * (1.0 - u_error * (0.10 + 0.10 * sin(u_time * 15.0)));

    vec3 bronze = vec3(0.34, 0.105, 0.010);
    vec3 amber = vec3(1.00, 0.405, 0.025);
    vec3 gold = vec3(1.00, 0.72, 0.18);
    vec3 whiteGold = vec3(1.00, 0.94, 0.63);
    vec3 color = vec3(0.0);
    float alpha = 0.0;

    // A faint translucent sphere, deliberately simple so idle rendering is cheap.
    float sphere = 1.0 - smoothstep(0.55, 0.86, radius);
    addLight(color, alpha, bronze, sphere * (0.08 + energy * 0.045));
    addLight(color, alpha, amber, ring(radius, 0.790, 0.006, aa) * 0.68);
    addLight(color, alpha, gold, ring(radius, 0.742, 0.0035, aa) * 0.38);

    // Three independently rotating segmented orbital shells.
    float outer = ring(radius, 0.855, 0.009, aa)
                * dash(angle, 20.0, t * 0.075, 0.16);
    float middle = ring(radius, 0.685 + energy * 0.018, 0.007, aa)
                 * dash(angle, 14.0, -t * 0.105, 0.20);
    float inner = ring(radius, 0.505, 0.005, aa)
                * dash(angle, 9.0, t * 0.135, 0.13);
    addLight(color, alpha, amber, outer * (0.68 + energy * 0.22));
    addLight(color, alpha, gold, middle * (0.60 + energy * 0.30));
    addLight(color, alpha, whiteGold, inner * (0.40 + energy * 0.28));

    // Tilted great-circle bands give the lightweight 2-D shader a 3-D hologram read.
    float ca = cos(t * 0.24);
    float sa = sin(t * 0.24);
    vec2 q1 = vec2(ca * p.x - sa * p.y, sa * p.x + ca * p.y);
    float cb = cos(-t * 0.17 + 1.2);
    float sb = sin(-t * 0.17 + 1.2);
    vec2 q2 = vec2(cb * p.x - sb * p.y, sb * p.x + cb * p.y);
    float orbitA = ellipseRing(q1, 0.34, 0.72, 0.009, aa)
                 * dash(atan(q1.y / 0.34, q1.x), 12.0, t * 0.055, 0.19);
    float orbitB = ellipseRing(q2, 0.48, 0.64, 0.007, aa)
                 * dash(atan(q2.y / 0.48, q2.x), 10.0, -t * 0.065, 0.17);
    addLight(color, alpha, gold, orbitA * (0.62 + energy * 0.24));
    addLight(color, alpha, amber, orbitB * (0.52 + energy * 0.22));

    // A rotating spiral-like pair of arcs makes the movement readable at a glance.
    float spiralWave = 0.39 + 0.105 * sin(angle * 2.0 - t * 1.35);
    float spiral = ring(radius, spiralWave, 0.008, aa)
                 * (1.0 - smoothstep(0.62, 0.76, radius));
    float counterWave = 0.29 + 0.075 * sin(angle * 3.0 + t * 1.05);
    float counter = ring(radius, counterWave, 0.006, aa)
                  * (1.0 - smoothstep(0.45, 0.61, radius));
    addLight(color, alpha, amber, spiral * 0.62);
    addLight(color, alpha, gold, counter * 0.50);

    // One scanning radial spoke reinforces rotation without expensive noise/raymarching.
    float scanAngle = atan(sin(angle - t * 0.92), cos(angle - t * 0.92));
    float scanner = (1.0 - smoothstep(0.018, 0.060, abs(scanAngle)))
                  * smoothstep(0.16, 0.25, radius)
                  * (1.0 - smoothstep(0.69, 0.80, radius));
    addLight(color, alpha, whiteGold, scanner * (0.28 + engaged * 0.22));

    // Bright central intelligence node, reactive to actual input/output audio.
    float coreRadius = 0.095 + voice * max(u_listening, u_speaking) * 0.040;
    float core = 1.0 - smoothstep(coreRadius, coreRadius + aa * 2.5, radius);
    float coreHalo = (1.0 - smoothstep(coreRadius, 0.31 + energy * 0.045, radius))
                   * (0.22 + energy * 0.18);
    addLight(color, alpha, amber, coreHalo);
    addLight(color, alpha, gold, ring(radius, coreRadius + 0.028, 0.010, aa) * 0.92);
    addLight(color, alpha, whiteGold, core * (0.84 + u_peak_impulse * 0.16));

    if (u_droplets > 0.5) {
        float nodes = ring(radius, 0.895, 0.012, aa)
                    * smoothstep(0.94, 0.992, cos(angle * 12.0 - t * 1.10));
        addLight(color, alpha, gold, nodes * (0.42 + energy * 0.28));
    }

    color *= brightness;
    color += vec3(1.0, 0.08, 0.02) * u_error
           * ring(radius, 0.79, 0.013, aa) * 0.48;
    color = color / (vec3(1.0) + color * 0.43);
    // Visibility 100% preserves the original appearance; 200% doubles both
    // emitted light and opacity for bright or high-ambient-light screens.
    float visibility = clamp(u_opacity, 0.2, 2.0);
    gl_FragColor = vec4(
        color * visibility,
        clamp(alpha * brightness * visibility, 0.0, 1.0)
    );
}
