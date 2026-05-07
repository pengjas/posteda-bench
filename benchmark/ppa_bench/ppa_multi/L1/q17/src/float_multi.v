module float_multi(clk, rst, a, b, z);

input clk, rst;
input [31:0] a, b;
output reg [31:0] z;

reg [2:0] counter;

reg [23:0] a_mantissa, b_mantissa, z_mantissa;
reg [9:0] a_exponent, b_exponent, z_exponent;
reg a_sign, b_sign, z_sign;

reg [49:0] product;

reg guard_bit, round_bit, sticky;

// Counter logic
always @(posedge clk or posedge rst) begin
    if(rst)
        counter <= 0;
    else
        counter <= counter + 1;
end

// Stage 1: IEEE 754 Representation extraction
always @(posedge clk or posedge rst) begin
    if(rst) begin
        a_mantissa <= 0;
        b_mantissa <= 0;
        a_exponent <= 0;
        b_exponent <= 0;
        a_sign <= 0;
        b_sign <= 0;
    end
    else if(counter == 3'b001) begin
        a_mantissa <= {1'b0, a[22:0]};
        b_mantissa <= {1'b0, b[22:0]};
        a_exponent <= a[30:23] - 127;
        b_exponent <= b[30:23] - 127;
        a_sign <= a[31];
        b_sign <= b[31];
    end
    else if(counter == 3'b010) begin
        if (!((a_exponent == 128 && a_mantissa[22:0] != 0) || (b_exponent == 128 && b_mantissa[22:0] != 0) ||
              (a_exponent == 128) || (b_exponent == 128) ||
              (($signed(a_exponent) == -127) && (a_mantissa[22:0] == 0)) ||
              (($signed(b_exponent) == -127) && (b_mantissa[22:0] == 0)))) begin
            if ($signed(a_exponent) == -127)
                a_exponent <= -126;
            else
                a_mantissa[23] <= 1;

            if ($signed(b_exponent) == -127)
                b_exponent <= -126;
            else
                b_mantissa[23] <= 1;
        end
    end
    else if(counter == 3'b011) begin
        if (~a_mantissa[23]) begin
            a_mantissa <= a_mantissa << 1;
            a_exponent <= a_exponent - 1;
        end
        if (~b_mantissa[23]) begin
            b_mantissa <= b_mantissa << 1;
            b_exponent <= b_exponent - 1;
        end
    end
end

// Stage 4: Multiplication
always @(posedge clk or posedge rst) begin
    if(rst) begin
        z_sign <= 0;
        z_exponent <= 0;
        product <= 0;
    end
    else if(counter == 3'b100) begin
        z_sign <= a_sign ^ b_sign;
        z_exponent <= a_exponent + b_exponent + 1;
        product <= a_mantissa * b_mantissa * 4;
    end
end

// Stage 5: Extract mantissa and rounding bits
always @(posedge clk or posedge rst) begin
    if(rst) begin
        z_mantissa <= 0;
        guard_bit <= 0;
        round_bit <= 0;
        sticky <= 0;
    end
    else if(counter == 3'b101) begin
        z_mantissa <= product[49:26];
        guard_bit <= product[25];
        round_bit <= product[24];
        sticky <= (product[23:0] != 0);
    end
    else if(counter == 3'b110) begin
        if ($signed(z_exponent) < -126) begin
            z_mantissa <= z_mantissa >> (-126 - $signed(z_exponent));
            guard_bit <= z_mantissa[0];
            round_bit <= guard_bit;
            sticky <= sticky | round_bit;
        end
        else if (z_mantissa[23] == 0) begin
            z_mantissa <= {z_mantissa[22:0], guard_bit};
            guard_bit <= round_bit;
            round_bit <= 0;
        end
        else if (guard_bit && (round_bit | sticky | z_mantissa[0])) begin
            z_mantissa <= z_mantissa + 1;
        end
    end
end

// Stage 6: Normalize exponent
always @(posedge clk or posedge rst) begin
    if(rst) begin
        // z_exponent handled in stage 4
    end
    else if(counter == 3'b110) begin
        if ($signed(z_exponent) < -126) begin
            z_exponent <= -126;
        end
        else if (z_mantissa[23] == 0) begin
            z_exponent <= z_exponent - 1;
        end
        else if (guard_bit && (round_bit | sticky | z_mantissa[0])) begin
            if (z_mantissa == 24'hffffff)
                z_exponent <= z_exponent + 1;
        end
    end
end

// Stage 7: Final output
always @(posedge clk or posedge rst) begin
    if(rst) begin
        z <= 0;
    end
    else if(counter == 3'b010) begin
        // Handle special cases
        if ((a_exponent == 128 && a_mantissa[22:0] != 0) || (b_exponent == 128 && b_mantissa[22:0] != 0)) begin
            // NaN
            z <= {1'b1, 8'd255, 1'b1, 22'd0};
        end
        else if (a_exponent == 128) begin
            if (($signed(b_exponent) == -127) && (b_mantissa[22:0] == 0)) begin
                // NaN (Inf * 0)
                z <= {1'b1, 8'd255, 1'b1, 22'd0};
            end
            else begin
                // Inf
                z <= {a_sign ^ b_sign, 8'd255, 23'd0};
            end
        end
        else if (b_exponent == 128) begin
            if (($signed(a_exponent) == -127) && (a_mantissa[22:0] == 0)) begin
                // NaN (0 * Inf)
                z <= {1'b1, 8'd255, 1'b1, 22'd0};
            end
            else begin
                // Inf
                z <= {a_sign ^ b_sign, 8'd255, 23'd0};
            end
        end
        else if (($signed(a_exponent) == -127) && (a_mantissa[22:0] == 0)) begin
            // Zero
            z <= {a_sign ^ b_sign, 31'd0};
        end
        else if (($signed(b_exponent) == -127) && (b_mantissa[22:0] == 0)) begin
            // Zero
            z <= {a_sign ^ b_sign, 31'd0};
        end
    end
    else if(counter == 3'b111) begin
        if ($signed(z_exponent) > 127) begin
            // Overflow - return Inf
            z <= {z_sign, 8'd255, 23'd0};
        end
        else if ($signed(z_exponent) == -126 && z_mantissa[23] == 0) begin
            // Denormalized
            z <= {z_sign, 8'd0, z_mantissa[22:0]};
        end
        else begin
            // Normal
            z <= {z_sign, z_exponent[7:0] + 8'd127, z_mantissa[22:0]};
        end
    end
end

endmodule
